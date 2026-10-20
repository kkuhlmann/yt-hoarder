import tomllib
import uuid
from pathlib import Path

import yt_dlp.version
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError

from dependencies import get_required_user_id
from logger import logger
from models import DownloadJob, JobType, MediaDetails, MediaType, TaskRecord, TaskStatus, TaskType
from orchestrator import DIRECT_DOWNLOAD_PIPELINE_JOB, JobSpec, orch
from progress_publisher import publish_status_change
from repositories import download_jobs as dj_repo
from repositories import media_access as media_access_repo
from repositories import media_details as md_repo
from repositories import task_records as tr_repo
from repositories import users as user_repo
from repositories.task_records import DIRECT_DOWNLOAD_PRIORITY, SLOT_HOLDING_STATUSES
from schemas import DownloadJobDTO, DownloadRequest
from serializers import serialize_download_job
from ytdlp.urls import normalize_video_url

router = APIRouter()

# Reason recorded on the FAILED task when an owner re-requests a video they deleted
# without overwrite. The substring "previously deleted" is a stable coupling point:
# the frontend keys the retry dialog's overwrite pre-check off it (see TasksTable.tsx).
DELETED_RETRY_MESSAGE = 'Video was previously deleted — retry with overwrite to re-download.'

ALREADY_TRANSCRIBED_MESSAGE = (
    "You already have this video's transcript: it is in your Transcripts view and searchable."
)

CANCELLED_RETRY_MESSAGE = (
    'A cancelled download for this video still holds its place in the queue. '
    'Retry that task from the Tasks tab, or delete it and submit again.'
)

# Every status that stops the pipeline from building a chain for this URL: the
# ix_task_records_active_unique predicate, plus POSTPROCESSING from
# ACTIVE_DOWNLOAD_STATUSES (which _find_duplicate_active_tasks checks). A submission
# that gets past this check and hits one of these is dropped without a trace.
_URL_BLOCKING_STATUSES = [*SLOT_HOLDING_STATUSES, TaskStatus.POSTPROCESSING]

# Media statuses that leave no file behind, so a re-request needs no overwrite. A cancel
# deletes its partials and can never overwrite a COMPLETE row (_CANCELLABLE_MEDIA_STATUSES),
# which is what puts CANCELLED in the same position as SKIPPED here.
_NO_FILE_MEDIA_STATUSES = (TaskStatus.SKIPPED, TaskStatus.CANCELLED)


def get_app_version() -> str:
    try:
        # Docker mounts pyproject.toml to /etc/app/ to avoid conflict with /app bind mount
        pyproject_path = Path('/etc/app/pyproject.toml')
        if not pyproject_path.exists():
            # Fallback for local development
            pyproject_path = Path(__file__).parent.parent / 'pyproject.toml'
        with open(pyproject_path, 'rb') as f:
            config = tomllib.load(f)
            return config['project']['version']
    except (OSError, tomllib.TOMLDecodeError, KeyError):
        logger.warning('Failed to read app version', exc_info=True)
        return 'unknown'


def _canonical_job_url(dl_job_dict: dict) -> str:
    """One identity for the conflict lookups and the pipeline they feed.

    Without this a pasted youtu.be or /shorts/ link slips past the duplicate check,
    since MediaDetails is only ever keyed on the canonical watch?v= form. Playlist
    jobs pass through raw: their list= param is what expand_playlists_impl keys on,
    and normalize_video_url would strip it.
    """
    url = dl_job_dict['url']
    if dl_job_dict.get('download_playlist'):
        return url
    return normalize_video_url(url)


async def _refuse_superseded_transcript_only(
    dl_job: DownloadRequest, url: str, user_id: int
) -> None:
    """409 a transcript-only request for a video whose full media the user already searches.

    Indexing it again would only duplicate every search hit. A playlist URL is checked per
    video instead, by filter_completed_downloads.
    """
    if dl_job.media_type != MediaType.TRANSCRIPT or dl_job.download_playlist:
        return
    if await md_repo.has_full_transcript(url, user_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={'message': ALREADY_TRANSCRIBED_MESSAGE},
        )


async def _enforce_storage_quota(user_id: int, media_type: MediaType | None) -> None:
    # A transcript-only item keeps no media, so the storage limit doesn't apply to it.
    if media_type == MediaType.TRANSCRIPT:
        return
    user = await user_repo.get_user_by_id(user_id)
    if user is None or user.storage_limit_bytes is None:
        return
    usage = await user_repo.get_user_storage_usage(user_id)
    if usage >= user.storage_limit_bytes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                'message': 'Storage limit reached',
                'storage_used_bytes': usage,
                'storage_limit_bytes': user.storage_limit_bytes,
            },
        )


@router.post('/', status_code=status.HTTP_201_CREATED)
async def download_media(dl_job: DownloadRequest, user_id: int = Depends(get_required_user_id)):
    await _enforce_storage_quota(user_id, dl_job.media_type)

    dl_job_dto = DownloadJobDTO.from_orm(dl_job.model_dump())
    dl_job_dto.user_id = user_id
    dl_job_dict = serialize_download_job(dl_job_dto)

    dl_job_dict['url'] = _canonical_job_url(dl_job_dict)

    url = dl_job_dict.get('url')
    media_type = dl_job_dict.get('media_type')

    await _refuse_superseded_transcript_only(dl_job, url, user_id)

    existing_task = await tr_repo.find_one(
        {
            'task_type': TaskType.DOWNLOAD,
            'download_job_url': url,
            'media_type': media_type,
            'status': _URL_BLOCKING_STATUSES,
        }
    )

    if existing_task is not None:
        # A cancel deliberately keeps blocking this URL (see sync_release_cancelled_task_slot),
        # and that applies to whoever asks next, not just the user who cancelled — so this
        # branch comes before the ownership split.
        if existing_task.status == TaskStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    'message': CANCELLED_RETRY_MESSAGE,
                    'task_id': existing_task.task_id,
                },
            )
        if existing_task.user_id == user_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    'message': 'Download task already exists',
                    'task_id': existing_task.task_id,
                },
            )
        existing_md = await _get_media_for_task(existing_task.id)
        if existing_md and existing_md.id:
            await media_access_repo.add_access(user_id, existing_md.id)
            return {
                'message': 'Another user is already downloading this media. Access granted.',
                'media_details_id': existing_md.id,
            }
            # MediaDetails not created yet (task too early in chain) — let
            # the download proceed; filter_completed_downloads handles cross-user
            # dedup with access granting once the media is persisted.

    existing_md = await md_repo.get_media_details_by_url_and_media_type(url, media_type)
    if existing_md is not None:
        if existing_md.status in _NO_FILE_MEDIA_STATUSES:
            pass  # Fall through to the download chain — there is no file to overwrite
        elif existing_md.owner_id != user_id:
            await media_access_repo.add_access(user_id, existing_md.id)
            return {
                'message': 'Media already exists. Access granted.',
                'media_details_id': existing_md.id,
            }
        else:
            if not dl_job_dict.get('overwrite', False):
                message = (
                    f'Set overwrite to re-download media. Current status: {existing_md.status}'
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        'message': message,
                        'media_details_id': existing_md.id,
                    },
                )

    # Owner re-requesting a video they deliberately deleted: the pipeline would drop this
    # silently (filter_completed_downloads SKIP). Surface it instead — a retryable FAILED
    # task record plus a 409 whose message the frontend turns into a toast.
    if not dl_job_dict.get('overwrite', False):
        deleted_md = await md_repo.get_deleted_media_by_url_type_owner(url, media_type, user_id)
        if deleted_md is not None:
            failed_task_id = await _record_deleted_download_attempt(dl_job, deleted_md, user_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    'message': (
                        'This video was previously deleted. Enable "Overwrite" to re-download it.'
                    ),
                    'media_details_id': deleted_md.id,
                    'task_id': failed_task_id,
                },
            )

    placeholder_task_id = await _create_resolving_placeholder(
        dl_job_dict, dl_job_dto.media_type, user_id
    )

    # Pipeline: expand playlists -> filter completed -> fan out download chains
    await orch.submit(
        JobSpec(
            job_name=DIRECT_DOWNLOAD_PIPELINE_JOB,
            args=([dl_job_dict],),
            tracked=False,
            priority=DIRECT_DOWNLOAD_PRIORITY,
            user_id=user_id,
        )
    )
    return {'task_id': placeholder_task_id}


async def _create_resolving_placeholder(
    dl_job_dict: dict, media_type: MediaType | None, user_id: int
) -> str | None:
    """Insert the RESOLVING row this submission will become, before yt-dlp runs.

    The download chain's TaskRecords are only written at the *end* of populate, so
    without this the tasks table shows nothing until the metadata fetch (and, for a
    playlist, the whole enumeration) completes — a wait of minutes on a busy default
    lane. _persist_download_chain_state adopts this row rather than inserting its own,
    so the task_id the user sees never changes.

    pending_payload carries the job because until populate runs the work exists only as
    an in-memory JobHandle: both the pipeline and populate jobs are tracked=False. It is
    stamped with the new task_id *before* the insert — the payload is serialized at
    commit, so a placeholder_task_id added afterwards never reaches the DB, and a
    restart-resumed populate would insert a second download row against the slot this
    one still holds.

    Mutates dl_job_dict. Returns None when ix_task_records_active_unique already has a
    row for this URL; the _URL_BLOCKING_STATUSES check above 409s that case, so reaching
    here means a concurrent submission won the race — let it own the URL and carry on.
    """
    is_playlist = bool(dl_job_dict.get('download_playlist'))
    message = 'Enumerating playlist...' if is_playlist else 'Fetching video metadata...'
    task_id = str(uuid.uuid4())
    dl_job_dict['placeholder_task_id'] = task_id
    record = TaskRecord(
        task_id=task_id,
        task_type=TaskType.DOWNLOAD,
        status=TaskStatus.RESOLVING,
        status_message=message,
        title=dl_job_dict['url'],
        media_type=media_type,
        download_job_url=dl_job_dict['url'],
        pending_payload=dl_job_dict,
        priority=DIRECT_DOWNLOAD_PRIORITY,
        user_id=user_id,
    )
    try:
        await tr_repo.insert_task(record)
    except IntegrityError:
        logger.info(f'No placeholder for {dl_job_dict["url"]}: an active task already owns it')
        dl_job_dict['placeholder_task_id'] = None
        return None

    publish_status_change(record.task_id, TaskStatus.RESOLVING.value, message, user_id=user_id)
    return record.task_id


async def _record_deleted_download_attempt(
    dl_job: DownloadRequest, deleted_md: MediaDetails, user_id: int
) -> str:
    """Create a retryable FAILED download TaskRecord for a blocked deleted-media re-download.

    Wires the record for the existing retry-with-overwrite path: persists a DownloadJob for
    the deleted MediaDetails and repoints its download_task_record_id at the new record, so
    task_records.retry._fetch_download_job_serialized can resolve the job. Publishes an SSE
    status_change so an open Tasks tab shows the row live. Returns the new task_id.
    """
    record = TaskRecord(
        task_id=str(uuid.uuid4()),
        task_type=TaskType.DOWNLOAD,
        status=TaskStatus.FAILED,
        status_message=DELETED_RETRY_MESSAGE,
        title=deleted_md.title,
        channel=deleted_md.channel,
        release_timestamp=deleted_md.release_timestamp,
        media_type=dl_job.media_type,
        download_job_url=deleted_md.url,
        user_id=user_id,
    )
    record = await tr_repo.insert_task(record)

    # Persist a DownloadJob so retry can resolve one (multiple rows per MediaDetails are
    # normal; retry forces overwrite=True so the stored options don't matter).
    persisted_job = DownloadJob(
        url=deleted_md.url,
        audio_only=dl_job.audio_only,
        download_playlist=dl_job.download_playlist,
        overwrite=False,
        media_type=dl_job.media_type,
        channel=deleted_md.channel,
        title=deleted_md.title,
        job_type=JobType.NORMAL_DOWNLOAD,
        generate_transcript=dl_job.generate_transcript,
        download_quality=dl_job.download_quality,
        audio_quality=dl_job.audio_quality,
        media_details_id=deleted_md.id,
        user_id=user_id,
    )
    await dj_repo.add_download_job(persisted_job)

    # Repoint the FK so retry's MediaDetails lookup (download_task_record_id == record.id)
    # resolves. Transient — a successful retry re-writes this FK to its own chain.
    await md_repo.update_one(deleted_md.id, {'download_task_record_id': record.id})

    publish_status_change(
        record.task_id, TaskStatus.FAILED.value, DELETED_RETRY_MESSAGE, user_id=user_id
    )
    return record.task_id


async def _get_media_for_task(task_record_id: int) -> MediaDetails | None:
    """Find the MediaDetails associated with a download TaskRecord (via FK)."""
    return await md_repo.get_media_details_by_download_task_record_id(task_record_id)


@router.get('/version')
async def get_version_info():
    """Get version information for yt-hoarder and yt-dlp"""
    return {
        'app_version': get_app_version(),
        'ytdlp_version': yt_dlp.version.__version__,
    }
