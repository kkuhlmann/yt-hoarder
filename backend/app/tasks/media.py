import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum
from functools import wraps

from sqlalchemy.exc import IntegrityError

from logger import logger
from models import (
    DownloadJob,
    MediaDetails,
    MediaType,
    SourceType,
    TaskRecord,
    TaskStatus,
    TaskType,
    utc_now,
)
from progress_publisher import publish_status_change
from repositories import download_jobs as dj_repo
from repositories import media_access as media_access_repo
from repositories import media_details as md_repo
from repositories import subscription_access as subscription_access_repo
from repositories import task_records as tr_repo
from repositories import users as user_repo
from repositories.task_records import DIRECT_DOWNLOAD_PRIORITY, SUBSCRIPTION_DOWNLOAD_PRIORITY
from schemas import DownloadJobDTO, MediaDetailsDTO
from serializers import (
    deserialize_download_job,
    media_details_to_dto,
    serialize_download_job,
)
from tasks.sprites import ACTIVE_SPRITE_STATUSES
from ytdlp.info import (
    EXTRACTION_UNAVAILABLE,
    build_not_ready_message,
    get_channel_from_info,
    get_release_timestamp,
    get_url_info_with_failure,
    is_video_ready_for_download,
)
from ytdlp.urls import normalize_video_url


class FilterDecision(str, Enum):
    INCLUDE = 'INCLUDE'
    SKIP = 'SKIP'


_FILTER_SKIP_STATUSES = frozenset(
    {
        TaskStatus.COMPLETE,
        TaskStatus.POSTPROCESSING,
        TaskStatus.IN_PROGRESS,
        TaskStatus.QUEUED,
        TaskStatus.SKIPPED,
        TaskStatus.RETRY,
        TaskStatus.CANCELLED,
        TaskStatus.DELETED,
    }
)

# Active status sets for task existence checking
ACTIVE_DOWNLOAD_STATUSES = [
    TaskStatus.QUEUED,
    TaskStatus.POSTPROCESSING,
    TaskStatus.IN_PROGRESS,
    TaskStatus.RETRY,
    TaskStatus.CANCELLED,
]

ACTIVE_TRANSCRIPT_STATUSES = [
    TaskStatus.QUEUED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.RETRY,
    TaskStatus.CANCELLED,
]

# Statuses meaning a worker may still act on this URL's row. Hard-deleting the
# MediaDetails out from under such a chain orphans it: the queued download runs
# against a dangling id and never records file_path/status on the live row.
# CANCELLED is deliberately excluded — no worker will touch the row.
IN_FLIGHT_DOWNLOAD_STATUSES = [
    TaskStatus.QUEUED,
    TaskStatus.POSTPROCESSING,
    TaskStatus.IN_PROGRESS,
    TaskStatus.RETRY,
]


def _has_in_flight_download(url: str, media_type: str | None) -> bool:
    """True when a download task that may still run exists for this URL/media_type."""
    return (
        tr_repo.sync_find_active_by_url_and_type(
            url, media_type, TaskType.DOWNLOAD, IN_FLIGHT_DOWNLOAD_STATUSES
        )
        is not None
    )


_QUEUED_STATUS_MESSAGES = {
    TaskType.DOWNLOAD: 'Waiting in download queue...',
    TaskType.TRANSCRIPT_GENERATION: 'Waiting for transcript generation...',
    TaskType.SPRITE_GENERATION: 'Waiting for download to finish...',
}


def create_task_record_from_job(
    task_id: str,
    task_type: TaskType,
    download_job: DownloadJobDTO,
    upstream_task_ids: list[str] | None = None,
) -> TaskRecord:
    """
    Create a TaskRecord from a download job DTO with common field extraction.

    Args:
        task_id: The task ID
        task_type: Type of task (DOWNLOAD, TRANSCRIPT_GENERATION, etc.)
        download_job: The DownloadJobDTO
        upstream_task_ids: Optional list of upstream task IDs for dependency tracking

    Returns:
        A new TaskRecord instance
    """
    media_details = download_job.media_details
    status_message = _QUEUED_STATUS_MESSAGES.get(task_type, 'Queued for processing...')

    return TaskRecord(
        task_id=task_id,
        task_type=task_type,
        status=TaskStatus.QUEUED,
        status_message=status_message,
        title=media_details.title if media_details else None,
        channel=media_details.channel if media_details else None,
        release_timestamp=media_details.release_timestamp if media_details else None,
        media_type=download_job.media_type,
        upstream_task_ids=upstream_task_ids,
        download_job_url=download_job.url,
        user_id=download_job.user_id,
    )


def _record_not_ready_task(dto: DownloadJobDTO, info: dict, reason: str) -> None:
    """Create or refresh a visible NOT_READY placeholder TaskRecord for an unreleased video.

    Without this, deferred videos (live / upcoming premiere / post-live) vanish from
    the tasks table until the next subscription run picks them up. The placeholder is
    upserted per URL/media_type so repeated runs don't pile up duplicates, and it is
    soft-deleted by _persist_download_chain_state once the video airs.
    """
    media_type = dto.media_type.value if dto.media_type else None

    # An active task already covers this URL (including CANCELLED, so a dismissed
    # placeholder stays dismissed) — nothing to surface.
    if tr_repo.sync_find_active_by_url_and_type(
        dto.url, media_type, TaskType.DOWNLOAD, ACTIVE_DOWNLOAD_STATUSES
    ):
        logger.debug(f'Skipping NOT_READY placeholder for {dto.url}: active task exists')
        tr_repo.sync_retire_placeholder(
            dto.placeholder_task_id,
            TaskStatus.SKIPPED,
            'Another task is already downloading this video',
            soft_delete=True,
        )
        return

    title = info.get('title') or dto.title
    channel = get_channel_from_info(info) or dto.channel
    release_timestamp = get_release_timestamp(info)
    message = build_not_ready_message(reason, release_timestamp, bool(dto.subscription_id))
    refresh_fields = {
        'status': TaskStatus.NOT_READY,
        'status_message': message,
        'title': title,
        'channel': channel,
        'release_timestamp': release_timestamp,
    }

    existing = tr_repo.sync_find_latest_not_ready_task(dto.url, media_type)
    if existing:
        tr_repo.sync_update_one(existing.task_id, refresh_fields)
        task_id = existing.task_id
        # ix_task_records_not_ready_unique allows only one live NOT_READY row per URL,
        # so the submission's placeholder yields to the one already there.
        tr_repo.sync_retire_placeholder(
            dto.placeholder_task_id, TaskStatus.SKIPPED, message, soft_delete=True
        )
    elif dto.placeholder_task_id:
        # The submission already has a row in the table — turn that one NOT_READY
        # rather than adding a second one for the same URL.
        tr_repo.sync_retire_placeholder(
            dto.placeholder_task_id,
            TaskStatus.NOT_READY,
            message,
            fields={
                'title': title,
                'channel': channel,
                'release_timestamp': release_timestamp,
            },
        )
        task_id = dto.placeholder_task_id
    else:
        record = TaskRecord(
            task_id=str(uuid.uuid4()),
            task_type=TaskType.DOWNLOAD,
            status=TaskStatus.NOT_READY,
            status_message=message,
            title=title,
            channel=channel,
            release_timestamp=release_timestamp,
            media_type=dto.media_type,
            download_job_url=dto.url,
            user_id=dto.user_id,
        )
        try:
            tr_repo.sync_insert_task(record)
            task_id = record.task_id
        except IntegrityError:
            # A sibling chain deferred this URL in the same tick and won the insert
            # (ix_task_records_not_ready_unique) — refresh its placeholder instead.
            existing = tr_repo.sync_find_latest_not_ready_task(dto.url, media_type)
            if existing is None:
                return
            tr_repo.sync_update_one(existing.task_id, refresh_fields)
            task_id = existing.task_id

    publish_status_change(task_id, TaskStatus.NOT_READY.value, message, user_id=dto.user_id)


# Re-check cadences for deferred videos, keyed on how long the row has already been
# deferred. Unreleased videos are cheap to re-check and may air at any time; a video
# yt-dlp positively reports as gone (private/members-only/geo-blocked/age-gated) is
# checked far less often. Both are (max_age, delay) pairs, first match wins.
_SHORT_DEFERRAL_LADDER = (
    (timedelta(hours=1), timedelta(minutes=10)),
    (timedelta(hours=6), timedelta(minutes=30)),
    (timedelta(days=1), timedelta(hours=2)),
    (timedelta(days=7), timedelta(hours=6)),
)
_SHORT_DEFERRAL_MAX = timedelta(hours=12)

_LONG_DEFERRAL_LADDER = (
    (timedelta(days=1), timedelta(hours=6)),
    (timedelta(days=7), timedelta(days=1)),
    (timedelta(days=30), timedelta(days=3)),
)
_LONG_DEFERRAL_MAX = timedelta(days=7)


def _to_utc_naive(value: datetime) -> datetime:
    """Normalize a release timestamp to the naive-UTC basis next_check_at is stored in.

    get_release_timestamp builds naive *local* datetimes (datetime.fromtimestamp /
    strptime), while utc_now() is naive UTC — comparing them directly skews by the
    host's UTC offset. astimezone() reads a naive input as local time, which is exactly
    the assumption those constructors made.
    """
    return value.astimezone(UTC).replace(tzinfo=None)


def _ladder_delay(age: timedelta, ladder: tuple, maximum: timedelta) -> timedelta:
    for max_age, delay in ladder:
        if age < max_age:
            return delay
    return maximum


def _next_check_at(
    existing: MediaDetails | None, release_timestamp: datetime | None, failure_kind: str | None
) -> datetime:
    """When a deferred URL should next be re-evaluated.

    A known future premiere schedules the re-check for the moment it airs. Otherwise the
    delay escalates with the row's age, so a video deferred for weeks stops costing a
    yt-dlp call every tick. The long ladder is opt-in: only a positively-identified
    permanent failure takes it, because a rate-limit block is indistinguishable from a
    private video and parking a transient failure for days is the worse error.
    """
    now = utc_now()

    if failure_kind != EXTRACTION_UNAVAILABLE and release_timestamp is not None:
        release_utc = _to_utc_naive(release_timestamp)
        if release_utc > now:
            return release_utc

    age = now - existing.created_at if existing else timedelta(0)
    if failure_kind == EXTRACTION_UNAVAILABLE:
        return now + _ladder_delay(age, _LONG_DEFERRAL_LADDER, _LONG_DEFERRAL_MAX)
    return now + _ladder_delay(age, _SHORT_DEFERRAL_LADDER, _SHORT_DEFERRAL_MAX)


def _defer_media(
    dto: DownloadJobDTO, info: dict, reason: str, failure_kind: str | None = None
) -> None:
    """Park a video that can't be downloaded yet: persist why, and when to look again.

    Writes a NOT_READY MediaDetails carrying the expected release time (so the wait is
    recorded, not just the fact of waiting) plus a next_check_at the subscription filter
    honours, and refreshes the tasks-table placeholder. Without the persisted row the
    URL has nothing to match on, so every tick re-fetches it forever.
    """
    media_type = dto.media_type.value if dto.media_type else None
    release_timestamp = get_release_timestamp(info) if info else None

    existing = md_repo.sync_get_media_details_by_url_and_media_type(dto.url, media_type)
    next_check_at = _next_check_at(existing, release_timestamp, failure_kind)

    md_repo.sync_upsert_deferred_media(
        MediaDetails(
            url=dto.url,
            media_type=dto.media_type,
            channel=get_channel_from_info(info) or dto.channel,
            title=(info.get('title') if info else None) or dto.title,
            release_timestamp=release_timestamp,
            duration=info.get('duration') if info else None,
            owner_id=dto.user_id,
        ),
        next_check_at,
    )
    logger.info(f'Deferred {dto.url} ({reason}); next check at {next_check_at}')

    _record_not_ready_task(dto, info, reason)


def _resolve_live_media_details_id(
    dto: DownloadJobDTO, media_type: str | None
) -> tuple[bool, int | None]:
    """Re-resolve the live MediaDetails id for this URL just before persisting.

    The payload's media_details.id can be stale: an overwrite sibling may have deleted
    the row since populate time. Returns (superseded, media_details_id):
    - (False, None): the payload carries no MediaDetails — nothing to re-resolve.
    - (False, id):   the row still exists; use this live id, not the payload id.
    - (True, None):  the payload referenced a row that has since been deleted — the
                     caller must abort so the sibling's chain owns the download.
    """
    if not (dto.media_details and dto.media_details.id):
        return False, None
    current_md = md_repo.sync_get_media_details_by_url_and_media_type(dto.url, media_type)
    if current_md is None:
        logger.info(
            f'Skipping download chain for {dto.url}: MediaDetails no longer exists (superseded)'
        )
        return True, None
    return False, current_md.id


def _link_media_details_to_tasks(
    media_details_id: int | None,
    download_record_id: int | None,
    transcript_record_id: int | None,
) -> None:
    """Write the download/transcript task-record FK references back onto MediaDetails.

    No-op when there's no live MediaDetails row to update.
    """
    if media_details_id is None:
        return
    update_fields = {}
    if download_record_id is not None:
        update_fields['download_task_record_id'] = download_record_id
    if transcript_record_id is not None:
        update_fields['transcript_task_record_id'] = transcript_record_id
    if update_fields:
        md_repo.sync_update_one(media_details_id, update_fields)


def _persist_download_job_for_retry(
    dto: DownloadJobDTO, media_details_id: int, inserted_task_ids: list[str]
) -> bool:
    """Persist a DownloadJob row for retry support. Returns False on rollback.

    Extremely narrow TOCTOU: if the MediaDetails was deleted between the live-id
    re-resolve and this insert (an overwrite sibling), the FK insert raises
    IntegrityError. We then roll back the task records just created so no orphaned
    QUEUED rows survive and return False so the caller aborts — the sibling's chain
    owns the download.
    """
    persisted_job = DownloadJob(
        url=dto.url,
        audio_only=dto.audio_only,
        download_playlist=dto.download_playlist,
        overwrite=dto.overwrite,
        media_type=dto.media_type,
        channel=dto.channel,
        title=dto.title,
        job_type=dto.job_type,
        generate_transcript=dto.generate_transcript,
        subscription_id=dto.subscription_id,
        media_details_id=media_details_id,
        user_id=dto.user_id,
    )
    try:
        dj_repo.sync_add_download_job(persisted_job)
    except IntegrityError:
        logger.warning(
            f'MediaDetails for {dto.url} deleted mid-persist; '
            f'rolling back {len(inserted_task_ids)} task record(s) and skipping'
        )
        tr_repo.sync_delete_tasks_by_ids(inserted_task_ids)
        return False
    else:
        return True


def _resolvable_placeholder(dto: DownloadJobDTO) -> TaskRecord | None:
    """The submit-time RESOLVING row this chain should adopt, if it still exists.

    A missing row means the user deleted the record — which removes the *record*, not
    the request — so the caller falls back to inserting a fresh download row.
    """
    if not dto.placeholder_task_id:
        return None
    return tr_repo.sync_get_task_by_task_id(dto.placeholder_task_id)


def _adopt_placeholder(task_id: str, dto: DownloadJobDTO) -> int | None:
    """Promote the RESOLVING placeholder to this chain's QUEUED download row.

    Updated in place rather than replaced so the task_id the user has been watching
    since submit survives into the download. Returns None when the row is no longer
    RESOLVING — a cancel landed during the metadata fetch, and the caller must stand
    down (nothing else can stop the chain: the placeholder task_id is never a
    JobSpec.task_id, so orch.cancel had nothing to dequeue).
    """
    media_details = dto.media_details
    record_id = tr_repo.sync_adopt_placeholder(
        task_id,
        {
            'status': TaskStatus.QUEUED,
            'status_message': _QUEUED_STATUS_MESSAGES[TaskType.DOWNLOAD],
            'title': media_details.title if media_details else dto.title,
            'channel': media_details.channel if media_details else dto.channel,
            'release_timestamp': media_details.release_timestamp if media_details else None,
        },
    )
    if record_id is None:
        logger.info(f'Standing down on {dto.url}: placeholder {task_id} is no longer RESOLVING')
    return record_id


def _abandon_adopted_placeholder(record_id: int | None, task_id: str) -> None:
    """Retire a just-adopted placeholder when the rest of the chain failed to persist.

    The adopted row is not in sync_insert_many_tasks' rollback set, so without this it
    survives as a QUEUED download whose chain was abandoned — and keeps holding the
    ix_task_records_active_unique slot for the URL.
    """
    if record_id is None:
        return
    tr_repo.sync_update_one(
        task_id,
        {
            'status': TaskStatus.SKIPPED,
            'status_message': 'Superseded by another download of this video',
        },
    )


def _build_chain_records(
    dto: DownloadJobDTO,
    download_task_id: str,
    transcript_task_id: str | None,
    *,
    create_download: bool,
    create_sprite: bool,
) -> tuple[TaskRecord | None, TaskRecord | None, list[TaskRecord]]:
    """The TaskRecords this chain still needs to insert, in insert order.

    The download record is absent when an adopted placeholder already is it.
    """
    download_task = None
    transcript_task = None
    task_documents = []

    if create_download:
        download_task = create_task_record_from_job(download_task_id, TaskType.DOWNLOAD, dto)
        task_documents.append(download_task)

    if transcript_task_id:
        transcript_task = create_task_record_from_job(
            transcript_task_id,
            TaskType.TRANSCRIPT_GENERATION,
            dto,
            upstream_task_ids=[download_task_id],
        )
        task_documents.append(transcript_task)

    if create_sprite:
        task_documents.append(
            create_task_record_from_job(
                str(uuid.uuid4()),
                TaskType.SPRITE_GENERATION,
                dto,
                upstream_task_ids=[download_task_id],
            )
        )

    return download_task, transcript_task, task_documents


def _persist_download_chain_state(
    dto: DownloadJobDTO,
    create_download_task: bool,
    create_transcript_task: bool,
    create_sprite_task: bool = False,
) -> tuple[str, str | None] | None:
    """Create task records and persist download state to the database.

    The sprite record is inserted QUEUED with no queue_sequence: it is dispatched
    later by DownloadHooks.on_success, and a null sequence is what marks it
    "not dispatched yet" for both startup recovery and the queue-position display.

    Returns (download_task_id, transcript_task_id_or_none) on success, None on IntegrityError.
    """
    placeholder = _resolvable_placeholder(dto) if create_download_task else None
    download_task_id = placeholder.task_id if placeholder else str(uuid.uuid4())
    transcript_task_id = str(uuid.uuid4()) if create_transcript_task else None

    download_task, transcript_task, task_documents = _build_chain_records(
        dto,
        download_task_id,
        transcript_task_id,
        create_download=create_download_task and placeholder is None,
        create_sprite=create_sprite_task,
    )

    if not task_documents and placeholder is None:
        return download_task_id, transcript_task_id

    media_type = dto.media_type.value if dto.media_type else None

    # Re-resolve the live MediaDetails id — the payload's id may be stale (deleted by
    # an overwrite sibling since populate time).
    superseded, media_details_id = _resolve_live_media_details_id(dto, media_type)
    if superseded:
        return None

    # The video is downloadable now — clear any lingering NOT_READY placeholder
    # so the fresh QUEUED chain replaces it in the tasks table.
    removed = tr_repo.sync_soft_delete_not_ready_tasks(dto.url, media_type)
    if removed:
        logger.info(f'Cleared {removed} stale NOT_READY task record(s) for {dto.url}')

    if create_sprite_task:
        # A CANCELLED sprite row still occupies ix_task_records_active_unique, and the
        # insert below is all-or-nothing — leaving it would silently drop the entire
        # download chain, not just the sheet. A new chain re-plans sprites.
        tr_repo.sync_release_cancelled_task_slot(dto.url, media_type, TaskType.SPRITE_GENERATION)

    download_record_id = None
    if placeholder is not None:
        download_record_id = _adopt_placeholder(placeholder.task_id, dto)
        if download_record_id is None:
            return None

    logger.debug(f'Inserting {len(task_documents)} task records into the database...')
    try:
        inserted_task_ids = tr_repo.sync_insert_many_tasks(task_documents)
    except IntegrityError:
        # Race condition: another worker already created a task for this URL
        # The partial unique index (ix_task_records_active_unique) prevents duplicates
        logger.info(f'Skipping task creation for {dto.url} due to existing active task')
        _abandon_adopted_placeholder(download_record_id, download_task_id)
        return None

    if download_record_id is None and download_task is not None:
        download_record_id = download_task.id

    _link_media_details_to_tasks(
        media_details_id, download_record_id, transcript_task.id if transcript_task else None
    )

    if (
        create_download_task
        and media_details_id is not None
        and not _persist_download_job_for_retry(dto, media_details_id, inserted_task_ids)
    ):
        _abandon_adopted_placeholder(download_record_id if placeholder else None, download_task_id)
        return None

    return download_task_id, transcript_task_id


def _find_duplicate_active_tasks(
    url: str, media_type: str | None, wants_transcript: bool, wants_sprites: bool = False
) -> tuple[bool, bool, bool, TaskRecord | None]:
    """Check for already-active download/transcript/sprite tasks for this URL.

    Returns (create_download_task, create_transcript_task, create_sprite_task,
    existing_download_task). The existing record is returned so the caller can rescue
    chains that were persisted but never dispatched (e.g. a worker crash before dispatch).
    """
    create_download_task = True
    create_transcript_task = wants_transcript
    create_sprite_task = wants_sprites

    existing_dl_task = tr_repo.sync_find_one(
        {
            'task_type': TaskType.DOWNLOAD,
            'download_job_url': url,
            'media_type': media_type,
            'status': ACTIVE_DOWNLOAD_STATUSES,
        }
    )
    if existing_dl_task:
        logger.debug(
            f'Skipping creation of new tasks for download job {url} '
            f'since an existing task with id {existing_dl_task.task_id} is already in progress.'
        )
        create_download_task = False

    if wants_transcript:
        existing_transcript_task = tr_repo.sync_find_one(
            {
                'task_type': TaskType.TRANSCRIPT_GENERATION,
                'download_job_url': url,
                'media_type': media_type,
                'status': ACTIVE_TRANSCRIPT_STATUSES,
            }
        )
        if existing_transcript_task:
            logger.debug(
                f'Skipping creation of new transcript task for download job {url} '
                f'since an existing transcript task with id {existing_transcript_task.task_id} '
                f'is already in progress.'
            )
            create_transcript_task = False

    if wants_sprites:
        existing_sprite_task = tr_repo.sync_find_one(
            {
                'task_type': TaskType.SPRITE_GENERATION,
                'download_job_url': url,
                'media_type': media_type,
                'status': ACTIVE_SPRITE_STATUSES,
            }
        )
        if existing_sprite_task:
            logger.debug(
                f'Skipping creation of new sprite task for download job {url} '
                f'since an existing sprite task with id {existing_sprite_task.task_id} '
                f'is already in progress.'
            )
            create_sprite_task = False

    return create_download_task, create_transcript_task, create_sprite_task, existing_dl_task


def _check_storage_quota(user_id: int | None, url: str) -> bool:
    """Returns True if the user's storage quota is exceeded and the download should be skipped."""
    if not user_id:
        return False
    quota_user = user_repo.sync_get_user_by_id(user_id)
    if not quota_user or quota_user.storage_limit_bytes is None:
        return False
    usage = user_repo.sync_get_user_storage_usage(user_id)
    if usage >= quota_user.storage_limit_bytes:
        logger.warning(
            f'Skipping download for user {user_id} ({url}): '
            f'storage quota exceeded ({usage} / {quota_user.storage_limit_bytes} bytes)'
        )
        return True
    return False


def _grant_shared_subscription_access(media_id: int, subscription_id: int | None) -> None:
    """Grant media access to all users shared on a subscription."""
    if not subscription_id:
        return
    shared_user_ids = subscription_access_repo.sync_get_users_with_access(subscription_id)
    if not shared_user_ids:
        return
    try:
        media_access_repo.sync_add_access_bulk(
            shared_user_ids,
            [media_id],
            source_type=SourceType.SUBSCRIPTION,
            source_id=subscription_id,
        )
    except IntegrityError:
        # Media row hard-deleted by a concurrent chain between our fetch and this
        # grant — skip; the next subscription tick re-grants against the new row.
        logger.warning(
            f'Skipping shared-subscription grants for media {media_id}: '
            f'row no longer exists (superseded by a concurrent chain)'
        )
        return
    logger.info(
        f'Granted media_access to {len(shared_user_ids)} shared subscription users '
        f'for media {media_id}'
    )


def _grant_media_access(
    user_id: int | None,
    media_id: int | None,
    source_type: SourceType = SourceType.DIRECT,
    source_id: int = 0,
    subscription_id: int | None = None,
) -> None:
    """Grant media access to a user, and optionally to shared subscription users.

    No-op if user_id or media_id is falsy. Idempotent (duplicate grants are ignored).
    """
    if not user_id or not media_id:
        return
    media_access_repo.sync_add_access(
        user_id, media_id, source_type=source_type, source_id=source_id
    )
    _grant_shared_subscription_access(media_id, subscription_id)


# --- filter_completed_downloads helpers ---


def _evaluate_skipped_media(dto: DownloadJobDTO, md: MediaDetailsDTO) -> FilterDecision:
    """Re-evaluate SKIPPED media to decide whether to include it for download.

    Direct downloads always bypass SKIPPED status. For subscriptions, re-evaluates
    against the current subscription's date and duration filters (the original skip
    may have been from a different subscription/user/filter, or filter settings may
    have changed since).
    """
    # Direct downloads always bypass SKIPPED status
    if not dto.subscription_id:
        logger.info(f'Allowing direct download past SKIPPED media for {dto.url}')
        return FilterDecision.INCLUDE

    if dto.subscription:
        # Re-evaluate against the CURRENT subscription's date filter
        if (
            dto.subscription.date_filter
            and md.release_timestamp
            and md.release_timestamp <= dto.subscription.date_filter
        ):
            logger.debug(f'Skipping {dto.url} - still filtered by subscription date_filter')
            # Still filtered — grant cross-user access
            if dto.user_id != md.owner_id:
                _grant_media_access(
                    dto.user_id,
                    md.id,
                    source_type=SourceType.SUBSCRIPTION,
                    source_id=dto.subscription_id or 0,
                )
            return FilterDecision.SKIP

        # Re-evaluate against the CURRENT subscription's duration filter
        if md.duration is not None:
            min_dur = dto.subscription.min_duration_seconds
            max_dur = dto.subscription.max_duration_seconds
            still_filtered = (min_dur is not None and md.duration < min_dur) or (
                max_dur is not None and md.duration > max_dur
            )
            if still_filtered:
                logger.debug(f'Skipping {dto.url} - still filtered by subscription duration filter')
                # Still filtered — grant cross-user access
                if dto.user_id != md.owner_id:
                    _grant_media_access(
                        dto.user_id,
                        md.id,
                        source_type=SourceType.SUBSCRIPTION,
                        source_id=dto.subscription_id or 0,
                    )
                return FilterDecision.SKIP

    # No filters still apply, or video now passes them
    logger.info(
        f'Re-evaluating previously SKIPPED media {dto.url} for subscription {dto.subscription_id}'
    )
    return FilterDecision.INCLUDE


def _handle_existing_media(dto: DownloadJobDTO, md: MediaDetailsDTO) -> FilterDecision:
    """Handle existing (non-SKIPPED) media: grant access and decide on overwrite.

    Grants cross-user media access for dedup, shared subscription access,
    and returns INCLUDE only if the overwrite flag is set.
    """
    if md.status == TaskStatus.DELETED:
        # Non-owner: re-download fresh instead of granting access to a dead record
        # (_reuse_or_delete_existing_media deletes it for recreation)
        if dto.user_id and dto.user_id != md.owner_id:
            logger.info(
                f'Re-downloading DELETED media {dto.url} for user {dto.user_id} '
                f'(owner={md.owner_id})'
            )
            return FilterDecision.INCLUDE
        # Owner: deletion was deliberate — no grants (incl. shared-sub users), skip
        # unless overwrite
        if dto.overwrite or (dto.subscription and dto.subscription.overwrite):
            logger.info(f'Re-downloading DELETED media {dto.url} (overwrite=True)')
            return FilterDecision.INCLUDE
        logger.debug(f'Skipping {dto.url} - owner deleted this media')
        return FilterDecision.SKIP

    # Cross-user dedup: grant access instead of re-downloading
    if dto.user_id and md.id and dto.user_id != md.owner_id:
        media_access_repo.sync_add_access(
            dto.user_id,
            md.id,
            source_type=SourceType.SUBSCRIPTION if dto.subscription_id else SourceType.DIRECT,
            source_id=dto.subscription_id or 0,
        )
        logger.info(
            f'Added media_access for user {dto.user_id} to existing media {md.id} '
            f'(owner={md.owner_id})'
        )

    # Also grant shared subscription users access to existing media
    _grant_shared_subscription_access(md.id, dto.subscription_id)

    if dto.overwrite or (dto.subscription and dto.subscription.overwrite):
        logger.info(f'Re-downloading {dto.url} (overwrite=True)')
        return FilterDecision.INCLUDE

    logger.debug(f'Skipping {dto.url} - status: {md.status}')
    return FilterDecision.SKIP


def _evaluate_not_ready_media(dto: DownloadJobDTO, md: MediaDetailsDTO) -> FilterDecision:
    """Hold a deferred video until its next_check_at has passed.

    Direct downloads bypass the backoff entirely: a user asking for this URL right now
    must not be silently dropped because a subscription parked it for a week.
    """
    if not dto.subscription_id:
        logger.info(f'Allowing direct download past deferred media for {dto.url}')
        return FilterDecision.INCLUDE

    if md.next_check_at is not None and md.next_check_at > utc_now():
        logger.debug(f'Skipping {dto.url} - deferred until {md.next_check_at}')
        return FilterDecision.SKIP

    return FilterDecision.INCLUDE


def _normalize_jobs(dl_jobs: list[dict]) -> list[DownloadJobDTO]:
    dtos = []
    for dj_dict in dl_jobs:
        if dj_dict is None:
            continue
        dto = deserialize_download_job(dj_dict)

        # /shorts/<id> and watch?v=<id> are the same video, but populate persists
        # only the canonical form — so an unnormalized key here misses forever and
        # the row gets deleted and re-fetched on every scan. Playlist expansion has
        # already run by this point; a still-unexpanded job keeps its list= param,
        # which normalize_video_url would strip.
        if not dto.download_playlist:
            dto = dto.model_copy(update={'url': normalize_video_url(dto.url)})
        dtos.append(dto)
    return dtos


def _load_existing_media(dtos: list[DownloadJobDTO]) -> dict[tuple[str, str | None], MediaDetails]:
    """Batch the per-URL existence lookup into one query per media type.

    A whole-channel enumeration is thousands of jobs, and this runs on the serial
    subscriptions lane on every tick — one round-trip each made it the dominant cost of
    the cycle.
    """
    urls_by_type: dict[str | None, list[str]] = {}
    for dto in dtos:
        media_type = dto.media_type.value if dto.media_type else None
        urls_by_type.setdefault(media_type, []).append(dto.url)

    existing: dict[tuple[str, str | None], MediaDetails] = {}
    for media_type, urls in urls_by_type.items():
        for url, md in md_repo.sync_get_media_details_by_urls(urls, media_type).items():
            existing[(url, media_type)] = md
    return existing


def _load_superseded_transcript_jobs(dtos: list[DownloadJobDTO]) -> set[tuple[int | None, str]]:
    """(user_id, url) of the transcript-only jobs whose user already has the full media's
    transcript to search, one query per user.

    Such a job is dropped without the cross-user grant _handle_existing_media would make:
    the grant would put the transcript-only row back in front of a user who has replaced
    it, on every subscription tick.
    """
    urls_by_user: dict[int | None, list[str]] = {}
    for dto in dtos:
        if dto.media_type == MediaType.TRANSCRIPT:
            urls_by_user.setdefault(dto.user_id, []).append(dto.url)
    return {
        (user_id, url)
        for user_id, urls in urls_by_user.items()
        for url in md_repo.sync_get_urls_with_full_transcript(urls, user_id)
    }


def filter_completed_downloads_impl(dl_jobs: list[dict] | None) -> list[dict]:
    """Drop jobs whose media already exists, is active, or isn't due yet; grant dedup access.

    An optimization, not a correctness boundary: anything that slips through is still
    caught by _find_duplicate_active_tasks and ix_task_records_active_unique.
    """
    dtos = _normalize_jobs(dl_jobs or [])
    existing = _load_existing_media(dtos)
    superseded = _load_superseded_transcript_jobs(dtos)

    filtered_dl_jobs = []
    for dto in dtos:
        if (dto.user_id, dto.url) in superseded and dto.media_type == MediaType.TRANSCRIPT:
            logger.debug(f'Skipping transcript-only {dto.url} - its full media is already indexed')
            tr_repo.sync_retire_placeholder(
                dto.placeholder_task_id,
                TaskStatus.SKIPPED,
                'Transcript already in your Transcripts view',
            )
            continue

        md = existing.get((dto.url, dto.media_type.value if dto.media_type else None))

        if md is None:
            filtered_dl_jobs.append(serialize_download_job(dto))
            continue

        logger.debug(f'Found existing media details: {md}')

        if md.status == TaskStatus.NOT_READY:
            decision = _evaluate_not_ready_media(dto, md)
        elif md.status not in _FILTER_SKIP_STATUSES:
            decision = FilterDecision.INCLUDE
        elif md.status == TaskStatus.SKIPPED:
            decision = _evaluate_skipped_media(dto, md)
        elif md.status == TaskStatus.CANCELLED and not dto.subscription_id:
            # A cancel leaves no file, so someone asking for this URL now is asking for a
            # fresh download, not a duplicate. Subscription jobs fall through and stay
            # skipped — that row is what stops the next tick resurrecting the cancel.
            decision = FilterDecision.INCLUDE
        else:
            decision = _handle_existing_media(dto, md)

        if decision == FilterDecision.INCLUDE:
            filtered_dl_jobs.append(serialize_download_job(dto))
        else:
            # No-op for subscription jobs, which carry no placeholder. Direct downloads
            # rarely land here (the not-ready and skipped branches hard-INCLUDE when
            # subscription_id is None), but a dropped job must never leave a row stuck.
            tr_repo.sync_retire_placeholder(
                dto.placeholder_task_id,
                TaskStatus.SKIPPED,
                f'Already in your library (status: {md.status.value})',
            )

    logger.debug(f'Filtered {len(filtered_dl_jobs)} download jobs from the original {len(dtos)}')

    return filtered_dl_jobs


# --- populate_media_details helper functions ---


def _resolve_and_validate_url(dto: DownloadJobDTO) -> DownloadJobDTO | None:
    """Normalize the video URL (canonicalize YouTube URLs, pass through others).

    Args:
        dto: The download job DTO with URL to resolve

    Returns:
        Updated DTO with normalized URL
    """
    resolved_url = normalize_video_url(dto.url)

    return DownloadJobDTO(
        **dto.model_dump(exclude={'url'}),
        url=resolved_url,
    )


def _delete_media_unless_in_flight(
    media_details: MediaDetails, url: str, media_type: str | None, reason_label: str
) -> bool:
    """Delete a MediaDetails row unless an in-flight chain still references it.

    Returns True if the row was deleted (the caller should drop its reference), or
    False if the delete was skipped because a queued/running download still holds the
    old id — deleting then would orphan that chain. CASCADE removes media_access rows
    and stale transcript blocks.
    """
    if _has_in_flight_download(url, media_type):
        logger.info(
            f'Skipping {reason_label} delete for {url}: an in-flight download chain '
            f'still references MediaDetails id={media_details.id}'
        )
        return False
    logger.info(f'Deleting {reason_label} MediaDetails id={media_details.id} for {url}')
    md_repo.sync_delete_by_url_and_media_type(media_details)
    return True


def _reuse_or_delete_existing_media(dto: DownloadJobDTO) -> DownloadJobDTO:
    """Check DB for existing MediaDetails; handle overwrite and SKIPPED deletion.

    Returns the DTO with media_details/existing_media_details_id populated if
    a valid existing record is found, or the DTO unchanged if no reusable record exists.
    """
    media_type = dto.media_type.value if dto.media_type else None
    media_details = md_repo.sync_get_media_details_by_url_and_media_type(dto.url, media_type)

    # If overwrite is set, delete existing MediaDetails (cascade deletes transcript_blocks) —
    # unless a chain is still in flight for this URL: deleting then would orphan it
    # (the queued download holds the old id), so let that chain finish instead.
    if (
        dto.overwrite
        and media_details
        and _delete_media_unless_in_flight(media_details, dto.url, media_type, 'overwrite')
    ):
        media_details = None

    # SKIPPED media has no files/transcripts — delete so it can be re-created fresh
    # with correct owner_id and access rows. Same for DELETED media when a different
    # user requests it (the owner's deletion must not hollow out other subscribers'
    # libraries). Safe because filter_completed_downloads already greenlit this
    # download. CASCADE deletes media_access rows and stale transcript blocks.
    # The in-flight guard applies here too: a sibling chain already re-downloading
    # this URL must not have its row deleted out from under it.
    if (
        media_details
        and (
            media_details.status == TaskStatus.SKIPPED
            or (
                media_details.status == TaskStatus.DELETED
                and dto.user_id
                and dto.user_id != media_details.owner_id
            )
        )
        and _delete_media_unless_in_flight(
            media_details, dto.url, media_type, str(media_details.status)
        )
    ):
        media_details = None

    # A deferred row must not satisfy either of _use_pending_or_fetch_fresh's early
    # returns: reuse below, and the pre-fetched-metadata shortcut. Both skip the
    # yt-dlp fetch, so is_video_ready_for_download never runs and an unreleased video
    # would be dispatched for download using the premiere metadata we stored.
    if media_details and media_details.status == TaskStatus.NOT_READY:
        logger.debug(f'Re-checking deferred media for {dto.url}: forcing a fresh fetch')
        return dto.model_copy(update={'pending_media_details': None})

    if media_details and media_details.release_timestamp:
        # Cross-user dedup: reusing another owner's record must also grant access
        # (the persist-time grant in _ensure_media_details_persisted is skipped on reuse)
        if dto.user_id and media_details.id and dto.user_id != media_details.owner_id:
            media_access_repo.sync_add_access(
                dto.user_id,
                media_details.id,
                source_type=SourceType.SUBSCRIPTION if dto.subscription_id else SourceType.DIRECT,
                source_id=dto.subscription_id or 0,
            )
        _grant_shared_subscription_access(media_details.id, dto.subscription_id)

        md_dto = media_details_to_dto(media_details)
        dto = DownloadJobDTO(
            **dto.model_dump(
                exclude={'media_details', 'existing_media_details_id', 'channel', 'title'}
            ),
            media_details=md_dto,
            existing_media_details_id=media_details.id,
            channel=dto.channel or media_details.channel,
            title=dto.title or media_details.title,
        )
        logger.debug(f'Reusing existing MediaDetails for {dto.url}')

    return dto


def _use_pending_or_fetch_fresh(dto: DownloadJobDTO) -> DownloadJobDTO | None:
    """Resolve media metadata from pre-fetched cache or fresh yt-dlp fetch.

    Skipped if dto already has media_details populated (from DB reuse).

    Returns:
        Updated DTO with pending_media_details populated, or None if yt-dlp failed.
    """
    # Already resolved from DB — nothing to do
    if dto.media_details:
        return dto

    # Use pre-fetched metadata from subscription call only if it's complete.
    # Missing duration usually means the video isn't released yet (upcoming
    # premiere / live), so fall through to a fresh fetch that can filter it out.
    if (
        dto.pending_media_details
        and dto.pending_media_details.release_timestamp
        and dto.pending_media_details.duration is not None
    ):
        logger.debug(f'Using pre-fetched metadata for {dto.url}')
        return dto

    # Fetch from yt-dlp
    info, failure_kind = get_url_info_with_failure(dto.url)
    if not info:
        logger.warning(f'Could not fetch info for {dto.url} ({failure_kind})')
        _defer_media(dto, {}, 'Video metadata is unavailable', failure_kind)
        return None

    # A channel/playlist URL reached the single-video path, and nothing downstream catches it:
    # is_video_ready_for_download inspects only live status, and create_ydl_options sets no
    # noplaylist, so the download would enumerate the whole channel into a single job. Deferring
    # instead would be wrong — a channel never becomes a video, so the NOT_READY row would make
    # every subscription tick re-fetch it forever.
    if info.get('_type') == 'playlist':
        logger.warning(f'Rejecting channel/playlist URL in the direct download path: {dto.url}')
        tr_repo.sync_retire_placeholder(
            dto.placeholder_task_id,
            TaskStatus.FAILED,
            'Channel and playlist URLs cannot be downloaded directly. Add as a subscription.',
        )
        return None

    # Defer unreleased videos (live / upcoming premiere / post-live) — don't
    # persist as downloadable until aired so metadata (duration, etc.) is captured
    # correctly. The deferred row records when it airs and when to look again.
    is_ready, reason = is_video_ready_for_download(info)
    if not is_ready:
        logger.info(f'Deferring {dto.url} — not released yet: {reason}')
        _defer_media(dto, info, reason)
        return None

    md_repo.sync_clear_deferral(dto.url, dto.media_type.value if dto.media_type else None)

    channel = get_channel_from_info(info) or dto.channel
    title = info.get('title') or dto.title
    release_timestamp = get_release_timestamp(info)
    duration = info.get('duration')

    pending_md_dto = MediaDetailsDTO(
        url=dto.url,
        media_type=dto.media_type,
        channel=channel,
        title=title,
        release_timestamp=release_timestamp,
        duration=duration,
        owner_id=dto.user_id,
    )

    logger.debug(
        f'Fetched from yt-dlp for {dto.url}: '
        f'title={title}, channel={channel}, release_timestamp={release_timestamp}'
    )

    return DownloadJobDTO(
        **dto.model_dump(exclude={'channel', 'title', 'pending_media_details'}),
        channel=channel,
        title=title,
        pending_media_details=pending_md_dto,
    )


def _fetch_or_reuse_media_details(dto: DownloadJobDTO) -> DownloadJobDTO | None:
    """Fetch media details from DB or yt-dlp, handling overwrite logic.

    Three-step fallback:
    1. Check DB for existing MediaDetails (with overwrite/SKIPPED deletion)
    2. Use pre-fetched metadata from subscription batch if available
    3. Fetch fresh metadata from yt-dlp

    Returns:
        Updated DTO with media details populated, or None if yt-dlp fetch failed.
    """
    dto = _reuse_or_delete_existing_media(dto)
    return _use_pending_or_fetch_fresh(dto)


def _apply_date_filter(dto: DownloadJobDTO) -> DownloadJobDTO | None:
    """Apply subscription date filter, persisting SKIPPED status if filtered.

    Args:
        dto: The download job DTO with media_details or pending_media_details

    Returns:
        The unchanged DTO if it passes the filter, or None if filtered out
    """
    working_md = dto.media_details or dto.pending_media_details

    if (
        dto.subscription
        and dto.subscription.date_filter is not None
        and (
            working_md
            and working_md.release_timestamp
            and working_md.release_timestamp <= dto.subscription.date_filter
        )
    ):
        logger.debug(
            f'Skipping {dto.url} - release_timestamp {working_md.release_timestamp} '
            f'is before subscription date_filter {dto.subscription.date_filter}'
        )
        # Persist the MediaDetails with SKIPPED status so we don't re-fetch next time
        if not dto.existing_media_details_id:
            skip_md = MediaDetails(
                url=working_md.url,
                media_type=working_md.media_type,
                channel=working_md.channel,
                title=working_md.title,
                release_timestamp=working_md.release_timestamp,
                duration=working_md.duration,
                status=TaskStatus.SKIPPED,
                owner_id=dto.user_id,
            )
            persisted_skip_md = md_repo.sync_upsert_media_details(skip_md)
            # Grant owner access so SKIPPED media appears in their "Show Skipped" view
            _grant_media_access(dto.user_id, persisted_skip_md.id)
        return None

    return dto


def _apply_duration_filter(dto: DownloadJobDTO) -> DownloadJobDTO | None:
    """Apply subscription duration filter, persisting SKIPPED status if filtered.

    Safety-net for cases where duration wasn't available during flat extraction
    (e.g. yt-dlp couldn't determine duration from the playlist page).

    Args:
        dto: The download job DTO with media_details or pending_media_details

    Returns:
        The unchanged DTO if it passes the filter, or None if filtered out
    """
    if not dto.subscription:
        return dto

    min_dur = dto.subscription.min_duration_seconds
    max_dur = dto.subscription.max_duration_seconds

    if min_dur is None and max_dur is None:
        return dto

    working_md = dto.media_details or dto.pending_media_details
    if not working_md or working_md.duration is None:
        # Duration unknown — can't filter, let it through
        return dto

    duration = working_md.duration
    filtered = False

    if min_dur is not None and duration < min_dur:
        logger.debug(
            f'Skipping {dto.url} - duration {duration}s is below min_duration_seconds {min_dur}s'
        )
        filtered = True
    elif max_dur is not None and duration > max_dur:
        logger.debug(
            f'Skipping {dto.url} - duration {duration}s is above max_duration_seconds {max_dur}s'
        )
        filtered = True

    if filtered:
        # Persist as SKIPPED so we don't re-fetch next time
        if not dto.existing_media_details_id:
            skip_md = MediaDetails(
                url=working_md.url,
                media_type=working_md.media_type,
                channel=working_md.channel,
                title=working_md.title,
                release_timestamp=working_md.release_timestamp,
                duration=working_md.duration,
                status=TaskStatus.SKIPPED,
                owner_id=dto.user_id,
            )
            persisted_skip_md = md_repo.sync_upsert_media_details(skip_md)
            _grant_media_access(dto.user_id, persisted_skip_md.id)
        return None

    return dto


def _ensure_media_details_persisted(dto: DownloadJobDTO) -> DownloadJobDTO:
    """Insert MediaDetails if it doesn't already exist (upsert).

    Args:
        dto: The download job DTO with pending_media_details

    Returns:
        Updated DTO with media_details and existing_media_details_id set
    """
    working_md = dto.media_details or dto.pending_media_details

    if not dto.existing_media_details_id and working_md:
        new_md = MediaDetails(
            url=working_md.url,
            media_type=working_md.media_type,
            channel=working_md.channel,
            title=working_md.title,
            release_timestamp=working_md.release_timestamp,
            duration=working_md.duration,
            owner_id=dto.user_id,
        )
        inserted_md = md_repo.sync_upsert_media_details(new_md)

        # Grant owner access + shared subscription users access
        _grant_media_access(
            dto.user_id,
            inserted_md.id,
            subscription_id=dto.subscription_id,
        )

        inserted_md_dto = media_details_to_dto(inserted_md)
        return DownloadJobDTO(
            **dto.model_dump(
                exclude={'media_details', 'existing_media_details_id', 'pending_media_details'}
            ),
            media_details=inserted_md_dto,
            existing_media_details_id=inserted_md.id,
            pending_media_details=None,
        )

    return dto


def _handle_playlist_creation(dto: DownloadJobDTO) -> None:
    """Auto-create playlist and add media if downloading from a YouTube playlist.

    Args:
        dto: The download job DTO with playlist_name and source_playlist_url
    """
    if not (dto.playlist_name and dto.source_playlist_url):
        return
    if dto.media_type == MediaType.TRANSCRIPT:
        # Playlists are for playing; a transcript-only item has nothing to play.
        return

    from repositories import playlists as playlist_repo

    playlist = playlist_repo.sync_get_playlist_by_source_url(dto.source_playlist_url)
    if not playlist:
        try:
            playlist = playlist_repo.sync_create_playlist(
                name=dto.playlist_name,
                source_url=dto.source_playlist_url,
                user_id=dto.user_id,
            )
            logger.info(f'Auto-created playlist "{dto.playlist_name}" (id={playlist.id})')
        except IntegrityError:
            # Get-or-create race: a parallel chain for another video of this playlist
            # created it first (uq_playlists_source_url) — reuse that one.
            playlist = playlist_repo.sync_get_playlist_by_source_url(dto.source_playlist_url)
            if playlist is None:
                raise
            logger.info(
                f'Reusing playlist "{playlist.name}" (id={playlist.id}) '
                f'created by a concurrent chain'
            )

    media_id = dto.existing_media_details_id or (
        dto.media_details.id if dto.media_details else None
    )
    if media_id:
        position = playlist_repo.sync_get_next_position(playlist.id)
        try:
            result = playlist_repo.sync_add_media_to_playlist(
                playlist_id=playlist.id,
                media_details_id=media_id,
                position=position,
            )
        except IntegrityError:
            # Concurrent chain added the same media first (uq_playlist_media)
            logger.info(f'Media {media_id} already in playlist {playlist.id} (concurrent add)')
            result = None
        if result:
            logger.debug(f'Added media {media_id} to playlist {playlist.id} at position {position}')


def populate_media_details_impl(dl_job: dict) -> dict | None:
    """
    Populates media details (channel, title, release_timestamp) and filters on upload date.
    Minimizes yt-dlp calls by reusing existing MediaDetails from database when available.
    Returns the serialized dl_job if it passes filters, None if it should be skipped.

    Pure resolution/filtering — the download-chain dispatch is the caller's job.
    """
    dto = deserialize_download_job(dl_job)

    # Step 1: Resolve and validate URL (skip shorts, etc.)
    dto = _resolve_and_validate_url(dto)
    if dto is None:
        return None

    # Step 2: Fetch or reuse existing MediaDetails
    dto = _fetch_or_reuse_media_details(dto)
    if dto is None:
        return None

    # Step 3: Apply date filter (skip old videos based on subscription settings)
    dto = _apply_date_filter(dto)
    if dto is None:
        return None

    # Step 3b: Apply duration filter (skip videos outside duration range)
    dto = _apply_duration_filter(dto)
    if dto is None:
        return None

    # Step 4: Persist MediaDetails if not already in DB
    dto = _ensure_media_details_persisted(dto)

    # Step 5: Handle auto-playlist creation
    _handle_playlist_creation(dto)

    return serialize_download_job(dto)


def run_populate_media_details(_ctx, dl_job: dict) -> dict | None:
    """Orchestrator body: populate, then create+dispatch the download chain directly."""
    result = populate_media_details_impl(dl_job)
    if result:
        create_download_and_transcript_chains_impl(result)
    return result


def guard_resolving_placeholders(fn):
    """Wrap a job body so no submission is ever left stranded in RESOLVING.

    Every early return and every raise inside the body lands here, and
    sync_retire_placeholder no-ops when a path already recorded a specific reason or
    the chain adopted the row.

    **Must wrap outside retry_transient_db**, not inside: retiring between attempts
    would leave the retry unable to adopt the row, so it would stand down instead of
    downloading.

    **Only for a body that owns the placeholder through to resolution** — never one that
    delegates it to another job. A delegating body returns while the job it handed off to
    is still queued, so this finally always beats the adoption and silently kills the
    chain. That is why run_direct_download_pipeline retires its own rows instead.
    """

    @wraps(fn)
    def wrapper(ctx, payload, *args, **kwargs):
        try:
            return fn(ctx, payload, *args, **kwargs)
        finally:
            jobs = payload if isinstance(payload, list) else [payload]
            for job in jobs:
                if isinstance(job, dict):
                    tr_repo.sync_retire_placeholder(
                        job.get('placeholder_task_id'),
                        TaskStatus.SKIPPED,
                        'Could not resolve this video',
                    )

    return wrapper


def create_download_and_transcript_chains_impl(download_job: dict) -> dict:
    """
    Receives a filtered download job, creates task records,
    then dispatches the download + transcript chain with explicit task IDs.
    """
    if not download_job:
        return {'message': 'No videos to process', 'count': 0}

    dto = deserialize_download_job(download_job)

    # A transcript-only item keeps no media, so the storage limit doesn't apply to it.
    if dto.media_type != MediaType.TRANSCRIPT and _check_storage_quota(dto.user_id, dto.url):
        tr_repo.sync_retire_placeholder(
            dto.placeholder_task_id, TaskStatus.FAILED, 'Storage limit reached'
        )
        return {'message': 'Storage quota exceeded', 'skipped': True}

    media_type = dto.media_type.value if dto.media_type else None
    wants_sprites = dto.media_type == MediaType.VIDEO
    (
        create_download_task,
        create_transcript_task,
        create_sprite_task,
        _existing_dl_task,
    ) = _find_duplicate_active_tasks(dto.url, media_type, dto.generate_transcript, wants_sprites)

    if not create_download_task:
        # A live task genuinely owns this URL — stand down. (Startup recovery
        # re-enqueues any persisted-but-undispatched QUEUED records, so there
        # is no crash window that strands them.)
        tr_repo.sync_retire_placeholder(
            dto.placeholder_task_id,
            TaskStatus.SKIPPED,
            'Another task is already downloading this video',
        )
        return {'download_queued': False, 'transcript_queued': create_transcript_task}

    result = _persist_download_chain_state(
        dto, create_download_task, create_transcript_task, create_sprite_task
    )
    if result is None:
        return {'message': 'Active task already exists', 'skipped': True}

    download_task_id, transcript_task_id = result
    priority = (
        DIRECT_DOWNLOAD_PRIORITY if dto.subscription_id is None else SUBSCRIPTION_DOWNLOAD_PRIORITY
    )
    tr_repo.dispatch_download_chain(
        download_data=download_job,
        download_task_id=download_task_id,
        transcript_task_id=transcript_task_id,
        priority=priority,
        download_status_msg=_QUEUED_STATUS_MESSAGES[TaskType.DOWNLOAD],
        transcript_status_msg=_QUEUED_STATUS_MESSAGES[TaskType.TRANSCRIPT_GENERATION],
        user_id=dto.user_id,
    )

    return {'download_queued': create_download_task, 'transcript_queued': create_transcript_task}
