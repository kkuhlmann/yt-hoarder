import contextlib
import uuid
from datetime import UTC
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from dependencies import (
    get_accessible_media_details,
    get_effective_user_id,
    get_entity_or_404,
    get_required_user_id,
)
from logger import logger
from models import LIBRARY_MEDIA_TYPES, MediaType, SourceType, TaskRecord, TaskStatus, TaskType
from orchestrator import TRANSCRIPT_JOB, JobSpec, orch
from progress_publisher import publish_status_change
from repositories import media_access as ma_repo
from repositories import media_details as md_repo
from repositories import playback_state as ps_repo
from repositories import playlists as playlists_repo
from repositories import ratings as ratings_repo
from repositories import settings as settings_repo
from repositories import tags as tags_repo
from repositories import task_records as tr_repo
from repositories import transcript_blocks as tb_repo
from repositories.task_records import DIRECT_DOWNLOAD_PRIORITY
from routers.sharing_routes import SharingConfig, register_sharing_routes
from schemas import (
    BulkMediaDeleteRequest,
    BulkTagRequest,
    MediaStats,
    PlaybackStateUpdate,
    RatingUpdateRequest,
    TagRenameRequest,
    TagSetRequest,
)
from serializers import media_details_to_dto, serialize_media_details
from services.embeddings import OnnxEmbedder
from services.transcript import get_hybrid_search_results
from utils import get_model
from ytdlp.files import delete_thumbnail
from ytdlp.urls import youtube_video_id

router = APIRouter()


def _media_types_filter(media_type: MediaType | None, has_transcript: bool) -> list | None:
    """The library lists only AUDIO/VIDEO unless a type is asked for by name. The
    Transcripts view (has_transcript) spans every type, as the transcript search does."""
    if media_type is not None:
        return [media_type]
    if has_transcript:
        return None
    return list(LIBRARY_MEDIA_TYPES)


def _parse_tag_ids(tag_ids: str | None) -> list[int] | None:
    """Parse the comma-separated tag_ids query param. Junk yields no filter, not a 422."""
    if not tag_ids:
        return None
    with contextlib.suppress(ValueError):
        return [int(t) for t in tag_ids.split(',') if t.strip()]
    return None


@router.get(
    '/stats',
    status_code=status.HTTP_200_OK,
    response_description='Media library statistics',
    response_model=MediaStats,
)
async def get_media_stats(
    search: str | None = None,
    status: str | None = None,
    media_type: MediaType | None = None,
    has_transcript: bool = False,
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get media library statistics, optionally filtered by search and status.

    Counts only audio and video unless `media_type` names a type. `has_transcript=true`
    counts the Transcripts view instead: everything with a transcript, of any type and
    status, including transcript-only items.

    Returns counts for:
    - total_downloads: count of matching MediaDetails
    - total_transcript_blocks: total transcript blocks for matching media
    - downloads_with_transcripts: matching downloads with at least one transcript block
    """
    stats = await md_repo.get_media_stats(
        search=search,
        status=status,
        user_id=effective_user_id,
        media_types=_media_types_filter(media_type, has_transcript),
        has_transcript=has_transcript,
    )
    return MediaStats(**stats)


@router.get(
    '/tags',
    status_code=status.HTTP_200_OK,
    response_description='List of user tags with usage counts',
)
async def get_user_tags(user_id: int = Depends(get_required_user_id)):
    """Get all tags for the current user, with usage counts."""
    return await tags_repo.get_user_tags(user_id)


@router.get(
    '/groups',
    status_code=status.HTTP_200_OK,
    response_description='Media grouped into folders with per-group stats',
)
async def get_media_groups_endpoint(
    group_by: str,
    level: str | None = None,
    parent: str | None = None,
    search: str | None = None,
    status: str = 'COMPLETE',
    tag_ids: str | None = None,
    min_rating: int | None = None,
    page: int = 1,
    page_size: int = 60,
    media_type: MediaType | None = None,
    has_transcript: bool = False,
    user_id: int = Depends(get_required_user_id),
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Group COMPLETE media into folders (by channel/tag/download or upload date).

    group_by: 'channel' | 'tag' | 'downloaded' | 'released'. Date dimensions use
    level ('year' | 'month'); 'month' requires parent=<year>. Respects the same
    search/tag/rating/access/type filters as the media list endpoint, including
    `has_transcript=true` for the Transcripts view's folders.
    """

    parsed_tag_ids = _parse_tag_ids(tag_ids)

    try:
        return await md_repo.get_media_groups(
            group_by=group_by,
            level=level,
            parent=parent,
            status=status,
            search=search,
            tag_ids=parsed_tag_ids,
            min_rating=min_rating,
            user_id=effective_user_id,
            rating_user_id=user_id,
            page=page,
            page_size=page_size,
            media_types=_media_types_filter(media_type, has_transcript),
            has_transcript=has_transcript,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get(
    '/{id}',
    status_code=status.HTTP_200_OK,
    response_description='MediaDetails object',
)
async def get_one_media_details(
    id: int,
    user_id: int = Depends(get_required_user_id),
    media_details=Depends(get_accessible_media_details),
):
    result = media_details.model_dump(mode='json')
    ps = await ps_repo.get_playback_state(user_id, id)
    result['playback_position'] = ps.playback_position if ps else None
    result['last_accessed'] = ps.last_accessed.isoformat() if ps and ps.last_accessed else None
    result['access_count'] = ps.access_count if ps else 0

    ratings = await ratings_repo.get_ratings_for_media_ids(user_id, [id])
    result['rating'] = ratings.get(id)
    tags = await tags_repo.get_tags_for_media_ids(user_id, [id])
    result['tags'] = tags.get(id, [])

    return result


@router.get(
    '/{id}/transcript',
    status_code=status.HTTP_200_OK,
    response_description='The full transcript of one media item',
)
async def get_media_transcript(
    user_id: int = Depends(get_required_user_id),
    media_details=Depends(get_accessible_media_details),
):
    """The whole transcript of an item the caller can see, for the media page.

    `media.playable` says whether the app holds a file it can stream from
    `/media/{id}`; when it doesn't (transcript-only items, deleted media that kept its
    transcript), `youtube_id` is the video to embed instead, or null for a source that
    isn't YouTube. `media.rating`, `media.access_count` and `media.playback_position`
    (where the caller left off, null if never played) are the caller's own.
    `media.transcript_task_status` / `transcript_task_progress` describe the latest
    transcription job, null if none ran; with `owner_id` they say whether the caller may
    start one (owner or admin, via `POST /media-details/transcripts/{id}/create`).
    `blocks` are in playback order, times in seconds.
    """
    blocks = await tb_repo.get_all_blocks(media_details.id)
    ratings = await ratings_repo.get_ratings_for_media_ids(user_id, [media_details.id])
    playback = await ps_repo.get_playback_state(user_id, media_details.id)
    transcript_task = (
        await tr_repo.get_task_by_id(media_details.transcript_task_record_id)
        if media_details.transcript_task_record_id
        else None
    )
    playable = md_repo.has_playable_file(media_details)
    return {
        'media': {
            'id': media_details.id,
            'url': media_details.url,
            'title': media_details.title,
            'channel': media_details.channel,
            'media_type': media_details.media_type,
            'status': media_details.status,
            'duration': media_details.duration,
            'release_timestamp': media_details.release_timestamp,
            'thumbnail_path': media_details.thumbnail_path,
            'playable': playable,
            'rating': ratings.get(media_details.id),
            'access_count': playback.access_count if playback else 0,
            'playback_position': playback.playback_position if playback else None,
            'owner_id': media_details.owner_id,
            'transcript_task_status': transcript_task.status if transcript_task else None,
            'transcript_task_progress': (
                transcript_task.percent_complete if transcript_task else None
            ),
        },
        'youtube_id': youtube_video_id(media_details.url),
        'blocks': [
            {'id': b.id, 'start_time': b.start_time, 'end_time': b.end_time, 'text': b.text}
            for b in blocks
        ],
    }


@router.get(
    '',
    status_code=status.HTTP_200_OK,
    response_description='MediaDetails objects matching criteria',
    response_model=dict[str, int | list[dict[str, Any]]],
)
async def get_all_media_details(
    search: str | None = None,
    status: str = 'COMPLETE',
    page: int = 1,
    page_size: int | None = None,
    sort_by: str | None = None,
    sort_direction: str = 'desc',
    tag_ids: str | None = None,
    min_rating: int | None = None,
    channel: str | None = None,
    untagged: bool = False,
    date_field: str | None = None,
    date_year: int | None = None,
    date_month: int | None = None,
    media_type: MediaType | None = None,
    has_transcript: bool = False,
    user_id: int = Depends(get_required_user_id),
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """List media the caller can see, filtered and paginated.

    Lists audio and video only, unless `media_type` names a type (`TRANSCRIPT` for
    transcript-only items). `has_transcript=true` is the Transcripts view: every item
    with a transcript, of any type and any status, including deleted media whose
    transcript was kept; each row then carries `transcript_block_count`.
    """
    if page_size is None:
        settings = await settings_repo.get_settings()
        page_size = settings.download_table_page_size
    logger.info(
        f'Router: get_all_media_details called with sort_by={sort_by}, sort_direction={sort_direction}'
    )

    parsed_tag_ids = _parse_tag_ids(tag_ids)

    return await md_repo.get_all_media_details(
        search=search,
        status=status,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_direction=sort_direction,
        user_id=effective_user_id,
        playback_user_id=user_id,
        tag_ids=parsed_tag_ids,
        min_rating=min_rating,
        rating_user_id=user_id,
        channel=channel,
        untagged=untagged,
        date_field=date_field,
        date_year=date_year,
        date_month=date_month,
        media_types=_media_types_filter(media_type, has_transcript),
        has_transcript=has_transcript,
    )


@router.patch(
    '/{id}/playback',
    status_code=status.HTTP_200_OK,
    response_description='Updated playback state',
    dependencies=[Depends(get_accessible_media_details)],
)
async def update_playback_state(
    id: int,
    update: PlaybackStateUpdate,
    user_id: int = Depends(get_required_user_id),
):
    """Update per-user playback state for a media item."""
    update_data = update.model_dump(exclude_unset=True)
    if not update_data:
        existing = await ps_repo.get_playback_state(user_id, id)
        return existing or {}

    if 'last_accessed' in update_data and update_data['last_accessed'] is not None:
        dt = update_data['last_accessed']
        if dt.tzinfo is not None:
            update_data['last_accessed'] = dt.astimezone(UTC).replace(tzinfo=None)

        # Debounced access_count increment: only count once per play session.
        # The player PATCHes every 5s, so we only increment when the previous
        # last_accessed is None or more than 60 seconds old.
        existing_ps = await ps_repo.get_playback_state(user_id, id)
        existing_last = existing_ps.last_accessed if existing_ps else None
        if (
            existing_last is None
            or (update_data['last_accessed'] - existing_last).total_seconds() > 60
        ):
            existing_count = existing_ps.access_count if existing_ps else 0
            update_data['access_count'] = existing_count + 1

    return await ps_repo.upsert_playback_state(user_id, id, update_data)


@router.get('/semantic/search', status_code=status.HTTP_200_OK)
async def semantic_search(
    semantic_search: str,
    standard_search: str | None = None,
    semantic_weight: float = 0.5,
    tag_ids: str | None = None,
    min_rating: int | None = None,
    channel: str | None = None,
    untagged: bool = False,
    date_field: str | None = None,
    date_year: int | None = None,
    date_month: int | None = None,
    model: OnnxEmbedder = Depends(get_model),
    user_id: int = Depends(get_required_user_id),
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Search transcripts, narrowed to the media the library filter currently selects.

    Takes the same filter params as the media list, so a group-folder drill-down,
    tag chips and a minimum rating all scope the search. `status` is deliberately
    absent: transcript search spans every status, and passing one would also swap the
    access tier to owner-only for DELETED/SKIPPED.
    """
    semantic_weight = max(0.0, min(1.0, semantic_weight))

    logger.debug(
        f'Hybrid search called with semantic_search="{semantic_search}", '
        f'standard_search="{standard_search}", semantic_weight={semantic_weight}'
    )
    return await get_hybrid_search_results(
        model,
        semantic_search,
        standard_search,
        semantic_weight=semantic_weight,
        user_id=effective_user_id,
        rating_user_id=user_id,
        tag_ids=_parse_tag_ids(tag_ids),
        min_rating=min_rating,
        channel=channel,
        untagged=untagged,
        date_field=date_field,
        date_year=date_year,
        date_month=date_month,
    )


@router.post('/transcripts/{id}/create', status_code=status.HTTP_200_OK)
async def create_transcript(
    request: Request, id: int, user_id: int = Depends(get_required_user_id)
):
    # Owner-only: generating a transcript enqueues a CPU-heavy Whisper job and mutates
    # the media record (transcript_task_record_id). Kept symmetric with the owner-only
    # delete_transcripts endpoint so a shared/read-only user can't drive either side.
    md = await get_entity_or_404(
        md_repo.get_media_details_by_id,
        id,
        'MediaDetails',
        access_check=partial(
            ma_repo.check_media_owner_or_raise, user_id, is_admin=request.state.is_admin
        ),
    )

    if md.media_type == MediaType.TRANSCRIPT and not md.file_path:
        # Its captions or audio were deleted once it was indexed; there is nothing here
        # to transcribe from. Requesting the URL again with Overwrite re-fetches them.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Transcript-only items are re-indexed by requesting the URL again '
            'with Overwrite',
        )

    if md.transcript_task_record_id:
        existing_task = await tr_repo.get_task_by_id(md.transcript_task_record_id)
        if existing_task and existing_task.status in [TaskStatus.QUEUED, TaskStatus.IN_PROGRESS]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    'message': 'Transcript generation already queued or in progress',
                    'transcript_status': existing_task.status.value,
                },
            )

    transcript_task_id = str(uuid.uuid4())

    existing_task_record = None
    if md.transcript_task_record_id:
        existing_task_record = await tr_repo.get_task_by_id(md.transcript_task_record_id)
        if existing_task_record and existing_task_record.status in [
            TaskStatus.CANCELLED,
            TaskStatus.FAILED,
        ]:
            # Set user_id to handle NULL-owner rows.
            await tr_repo.update_one(
                existing_task_record.task_id,
                {
                    'task_id': transcript_task_id,
                    'status': TaskStatus.QUEUED,
                    'status_message': 'Retrying transcript generation...',
                    'percent_complete': 0,
                    'title': md.title,
                    'channel': md.channel,
                    'release_timestamp': md.release_timestamp,
                    'media_type': md.media_type,
                    'user_id': user_id,
                },
            )
        else:
            existing_task_record = None

    if not existing_task_record:
        task_record = TaskRecord(
            task_id=transcript_task_id,
            task_type=TaskType.TRANSCRIPT_GENERATION,
            title=md.title,
            channel=md.channel,
            release_timestamp=md.release_timestamp,
            media_type=md.media_type,
            status=TaskStatus.QUEUED,
            user_id=user_id,
        )
        logger.debug(f'creating task record: {task_record}')
        # Insert task record first to get its ID
        await tr_repo.insert_task(task_record)
        md.transcript_task_record_id = task_record.id
        await md_repo.upsert_media_details(md)

    md_dto = media_details_to_dto(md)
    await orch.submit(
        JobSpec(
            job_name=TRANSCRIPT_JOB,
            args=(serialize_media_details(md_dto),),
            task_id=transcript_task_id,
            priority=DIRECT_DOWNLOAD_PRIORITY,
            user_id=user_id,
        )
    )

    publish_status_change(
        transcript_task_id,
        TaskStatus.QUEUED.value,
        'Queued for transcript generation...',
        user_id=user_id,
    )

    return {'task': transcript_task_id, 'status': TaskStatus.QUEUED.value}


async def _attempt_ownership_transfer(media_id: int, owner_id: int) -> bool:
    """Transfer media ownership to the next access holder. Returns True if transferred."""
    transfer_candidates = await ma_repo.get_transfer_candidate_user_ids(media_id, owner_id)
    if not transfer_candidates:
        return False
    new_owner_id = transfer_candidates[0]
    await md_repo.transfer_ownership(media_id, new_owner_id)
    # Owner-style DIRECT access so later unshare/unsubscribe cascades can't strip
    # the new owner's visibility of media they now own
    await ma_repo.add_access(new_owner_id, media_id)
    await ma_repo.remove_user_access_for_media(owner_id, media_id)
    logger.info(
        f'Transferred ownership of media {media_id} from user {owner_id} to user {new_owner_id}'
    )
    return True


async def _handle_soft_delete_cascade(media_id: int, keep_transcripts: bool) -> None:
    """Soft delete a media record with full cascade: transcripts, playlists, access rows.

    A thumbnail lives as long as the media file or the transcript does, so it is kept
    only when a transcript actually stays behind.
    """
    keep_thumbnail = keep_transcripts and await tb_repo.has_transcript_blocks(media_id)
    if not keep_transcripts:
        transcript_delete_count = await tb_repo.delete_transcript_block_by_media_details_id(
            media_id
        )
        logger.info(
            f'Deleted {transcript_delete_count} transcript_blocks for media_details_id: {media_id}'
        )
    else:
        logger.info(f'Keeping transcript blocks for media_details_id: {media_id}')

    playlist_remove_count = await playlists_repo.remove_media_from_all_playlists(media_id)
    if playlist_remove_count > 0:
        logger.info(
            f'Removed media_details_id: {media_id} from {playlist_remove_count} playlist(s)'
        )

    access_cleanup_count = await ma_repo.remove_all_access_for_media(media_id)
    if access_cleanup_count > 0:
        logger.info(f'Cleaned up {access_cleanup_count} media_access rows for media {media_id}')

    tag_cleanup_count = await tags_repo.remove_all_media_tags_for_media(media_id)
    if tag_cleanup_count > 0:
        logger.info(f'Cleaned up {tag_cleanup_count} media_tag rows for media {media_id}')

    md_modified_count = await md_repo.soft_delete_media_details_by_id(
        media_id, keep_thumbnail=keep_thumbnail
    )
    if md_modified_count > 0:
        logger.info(f'Soft deleted MediaDetails with id: {media_id}')
        return

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f'MediaDetails with id {media_id} not found',
    )


async def _delete_one_media(
    media_id: int, user_id: int, is_admin: bool, keep_transcripts: bool
) -> str:
    """Apply the single-item delete decision tree to one media id.

    Returns an outcome: 'deleted' | 'transferred' | 'access_removed' | 'forbidden'
    | 'not_found'. Shared by the single-item and bulk delete endpoints so behavior
    stays identical.
    """
    media = await md_repo.get_media_details_by_id(media_id)
    if media is None:
        return 'not_found'
    if media.media_type == MediaType.TRANSCRIPT:
        # The transcript is all a transcript-only item is. Kept, the DELETED row would
        # still be listed in the Transcripts view and found by search.
        keep_transcripts = False

    is_owner = media.owner_id == user_id

    if is_owner or is_admin:
        # Only check for transfer when the OWNER deletes their own media.
        # Admin deleting someone else's media = force delete (authoritative).
        if (
            is_owner
            and media.owner_id
            and await _attempt_ownership_transfer(media_id, media.owner_id)
        ):
            return 'transferred'
        await _handle_soft_delete_cascade(media_id, keep_transcripts)
        return 'deleted'

    has_direct = await ma_repo.has_direct_access(user_id, media_id)
    if has_direct:
        await ma_repo.remove_access(user_id, media_id, source_type=SourceType.DIRECT, source_id=0)
        await tags_repo.remove_user_media_tags_for_media(user_id, media_id)
        logger.info(f'Removed direct media_access for user {user_id} on media {media_id}')
        return 'access_removed'

    return 'forbidden'


@router.delete(
    '/bulk-delete',
    status_code=status.HTTP_200_OK,
    response_description='Delete multiple media items',
    response_model=dict,
)
async def bulk_delete_media_details(
    request: Request,
    bulk_req: BulkMediaDeleteRequest,
    user_id: int = Depends(get_required_user_id),
):
    """Delete multiple media items, applying the same per-item ownership/access rules
    as the single-item endpoint. Continues past per-item failures and reports counts."""
    is_admin = request.state.is_admin
    counts = {
        'deleted': 0,
        'transferred': 0,
        'access_removed': 0,
        'forbidden': 0,
        'not_found': 0,
    }
    errors: list[dict] = []
    for media_id in bulk_req.media_details_ids:
        try:
            outcome = await _delete_one_media(
                media_id, user_id, is_admin, bulk_req.keep_transcripts
            )
            counts[outcome] += 1
        except Exception as exc:  # one bad item must not abort the batch
            logger.exception(f'Bulk delete failed for media {media_id}')
            errors.append({'media_details_id': media_id, 'error': str(exc)})
    return {**counts, 'errors': errors}


@router.delete(
    '/{id}',
    status_code=status.HTTP_204_NO_CONTENT,
    response_description='MediaDetails deleted successfully',
)
async def delete_media_details(
    request: Request,
    id: int,
    keep_transcripts: bool = False,
    user_id: int = Depends(get_required_user_id),
):
    """
    Delete behavior depends on who is deleting:

    - **Owner or admin**: Soft delete the actual record (file + optional transcripts + mark DELETED)
    - **Non-owner with direct access**: Remove only their SourceType.DIRECT MediaAccess row
    - **Non-owner with only playlist/subscription access**: Return 403 (must remove at parent scope)
    """
    outcome = await _delete_one_media(id, user_id, request.state.is_admin, keep_transcripts)
    if outcome == 'not_found':
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'MediaDetails with id {id} not found',
        )
    if outcome == 'forbidden':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Cannot delete media that belongs to a shared playlist or subscription. Remove it from the source instead.',
        )
    return


@router.delete(
    '/{id}/hard',
    status_code=status.HTTP_204_NO_CONTENT,
    response_description='MediaDetails permanently deleted',
)
async def hard_delete_media_details(
    request: Request, id: int, user_id: int = Depends(get_required_user_id)
):
    """
    Hard delete a MediaDetails record (permanent row deletion).
    Only works on records that have already been soft-deleted (status=DELETED).
    Cascade will automatically delete transcript blocks and embeddings.
    Owner or admin only.
    """
    await get_entity_or_404(
        md_repo.get_media_details_by_id,
        id,
        'MediaDetails',
        access_check=partial(
            ma_repo.check_media_owner_or_raise, user_id, is_admin=request.state.is_admin
        ),
    )

    deleted_count = await md_repo.hard_delete_media_details_by_id(id)
    if deleted_count > 0:
        logger.info(f'Hard deleted MediaDetails with id: {id}')
        return

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f'MediaDetails with id {id} not found or not in DELETED status',
    )


@router.delete(
    '/{id}/transcripts',
    status_code=status.HTTP_200_OK,
    response_description='Transcript work removed (active task cancelled and/or blocks deleted)',
    response_model=dict,
)
async def delete_transcripts_for_media(
    request: Request, id: int, user_id: int = Depends(get_required_user_id)
):
    """
    Remove all transcript work for a media record:
    - If an active (QUEUED/IN_PROGRESS/POSTPROCESSING/RETRY) transcript task exists,
      cancel the job, mark the TaskRecord CANCELLED, and cascade downstream.
    - Delete all transcript blocks for the media (partial or completed).

    Does not affect the media record itself. Owner or admin only.
    """
    md = await get_entity_or_404(
        md_repo.get_media_details_by_id,
        id,
        'MediaDetails',
        access_check=partial(
            ma_repo.check_media_owner_or_raise, user_id, is_admin=request.state.is_admin
        ),
    )

    cancellable_statuses = {
        TaskStatus.QUEUED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.POSTPROCESSING,
        TaskStatus.RETRY,
    }
    task_cancelled = False
    downstream_tasks_cancelled = 0

    if md.transcript_task_record_id:
        task_record = await tr_repo.get_task_by_id(md.transcript_task_record_id)
        if task_record and task_record.status in cancellable_statuses:
            # Cancel the job: queued jobs are dequeued immediately; a running
            # transcription child is SIGTERM'd by the subprocess runner, with
            # the DB-poll cooperative cancellation inside generate_raw_segments
            # (set below) as the fallback at the next chunk boundary.
            await orch.cancel(task_record.task_id)

            await tr_repo.update_one(
                task_record.task_id,
                {'status': TaskStatus.CANCELLED, 'status_message': 'Cancelled by user'},
            )
            downstream_tasks_cancelled = await tr_repo.mark_downstream_as_cancelled(
                task_record.task_id
            )
            publish_status_change(
                task_record.task_id,
                TaskStatus.CANCELLED.value,
                'Cancelled by user',
                user_id=task_record.user_id,
            )
            task_cancelled = True

    blocks_deleted = await tb_repo.delete_transcript_block_by_media_details_id(id)

    # Clear the stale transcript_task_record link so the Downloads table renders
    # the "generate transcript" button again instead of the old COMPLETE/CANCELLED
    # icon. The TaskRecord row itself is preserved for history in the Tasks tab.
    cleared: dict = {}
    if md.transcript_task_record_id is not None:
        cleared['transcript_task_record_id'] = None
    # With no media file either, the transcript was the last thing the thumbnail was for.
    if not md.file_path and md.thumbnail_path:
        delete_thumbnail(md.thumbnail_path)
        cleared['thumbnail_path'] = None
    if cleared:
        await md_repo.update_one(id, cleared)

    logger.info(
        f'Removed transcript work for media_details_id={id}: '
        f'blocks_deleted={blocks_deleted}, task_cancelled={task_cancelled}, '
        f'downstream_tasks_cancelled={downstream_tasks_cancelled}'
    )

    return {
        'blocks_deleted': blocks_deleted,
        'task_cancelled': task_cancelled,
        'downstream_tasks_cancelled': downstream_tasks_cancelled,
    }


@router.put(
    '/bulk-tags',
    status_code=status.HTTP_200_OK,
    response_description='Add tags to multiple media items',
    response_model=dict,
)
async def bulk_add_media_tags(
    request: Request,
    body: BulkTagRequest,
    user_id: int = Depends(get_required_user_id),
):
    """Add tags to multiple media items in one call (unions with existing tags).

    Only media the user can access are tagged; the rest are reported as skipped.
    """
    is_admin = request.state.is_admin
    accessible_ids = []
    skipped = 0
    for media_id in body.media_details_ids:
        media = await md_repo.get_media_details_by_id(media_id)
        if media and await ma_repo.user_can_access_media(user_id, media, is_admin=is_admin):
            accessible_ids.append(media_id)
        else:
            skipped += 1

    result = await tags_repo.add_tags_to_media_bulk(user_id, accessible_ids, body.tag_names)
    return {**result, 'skipped': skipped}


@router.put(
    '/{id}/tags',
    status_code=status.HTTP_200_OK,
    response_description='Updated tags for the media item',
    dependencies=[Depends(get_accessible_media_details)],
)
async def set_media_tags(
    id: int,
    body: TagSetRequest,
    user_id: int = Depends(get_required_user_id),
):
    """Set tags for a media item. Creates missing tags on-the-fly."""
    return await tags_repo.set_media_tags(user_id, id, body.tag_names)


@router.patch(
    '/tags/{tag_id}',
    status_code=status.HTTP_200_OK,
    response_description='Renamed tag',
)
async def rename_tag(
    tag_id: int,
    body: TagRenameRequest,
    user_id: int = Depends(get_required_user_id),
):
    tag = await tags_repo.rename_tag(user_id, tag_id, body.name)
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Tag not found or name already exists',
        )
    return {'id': tag.id, 'name': tag.name}


@router.delete(
    '/tags/{tag_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    response_description='Tag deleted',
)
async def delete_tag(
    tag_id: int,
    user_id: int = Depends(get_required_user_id),
):
    """Delete a tag and all its associations."""
    deleted = await tags_repo.delete_tag(user_id, tag_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Tag not found',
        )


# --- Rating endpoints ---


@router.put(
    '/{id}/rating',
    status_code=status.HTTP_200_OK,
    response_description='Updated rating',
    dependencies=[Depends(get_accessible_media_details)],
)
async def set_rating(
    id: int,
    body: RatingUpdateRequest,
    user_id: int = Depends(get_required_user_id),
):
    """Set or update a rating for a media item."""
    if body.rating < 1 or body.rating > 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='Rating must be between 1 and 5',
        )
    rating = await ratings_repo.upsert_rating(user_id, id, body.rating)
    return {'media_details_id': id, 'rating': rating.rating}


@router.delete(
    '/{id}/rating',
    status_code=status.HTTP_204_NO_CONTENT,
    response_description='Rating removed',
    dependencies=[Depends(get_accessible_media_details)],
)
async def delete_rating(
    id: int,
    user_id: int = Depends(get_required_user_id),
):
    """Remove a rating for a media item."""
    await ratings_repo.delete_rating(user_id, id)


# --- Sharing endpoints ---


async def _grant_media(user_ids: list[int], media_details_id: int) -> None:
    for uid in user_ids:
        await ma_repo.add_access(uid, media_details_id)


async def _bulk_grant_media(user_ids: list[int], media_details_id: int) -> None:
    await ma_repo.add_access_bulk(user_ids, [media_details_id])


register_sharing_routes(
    router,
    SharingConfig(
        entity_name='MediaDetails',
        noun='media',
        doc_noun='media item',
        plural_slug='media',
        id_key='media_details_id',
        get_by_id=md_repo.get_media_details_by_id,
        check_owner_or_raise=ma_repo.check_media_owner_or_raise,
        grant=_grant_media,
        revoke=ma_repo.remove_access,
        list_user_ids=ma_repo.get_users_with_access,
        bulk_grant=_bulk_grant_media,
        bulk_note=(
            "Skips media the caller doesn't own (reported in errors). Media shares have no cascade."
        ),
    ),
)
