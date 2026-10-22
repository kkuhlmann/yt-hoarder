import uuid
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from dependencies import (
    get_accessible_clip,
    get_effective_user_id,
    get_entity_or_404,
    get_required_user_id,
)
from logger import logger
from models import (
    LIBRARY_MEDIA_TYPES,
    Clip,
    MediaDetails,
    MediaType,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from orchestrator import CLIP_FROM_SOURCE_JOB, CLIP_JOB, JobSpec, orch
from progress_publisher import publish_status_change
from repositories import clip_access as ca_repo
from repositories import clips as clips_repo
from repositories import media_access as ma_repo
from repositories import media_details as md_repo
from repositories import settings as settings_repo
from repositories import task_records as tr_repo
from repositories.task_records import DIRECT_DOWNLOAD_PRIORITY
from routers.sharing_routes import SharingConfig, register_sharing_routes
from ytdlp.urls import youtube_video_id

router = APIRouter()


class ClipCreate(BaseModel):
    """Request model for creating a clip."""

    media_details_id: int
    title: str
    description: str | None = None
    start_time: float
    end_time: float
    # The clip's output type. Required for a source with no file, which can be clipped
    # as either; a source with a file always yields its own type.
    media_type: MediaType | None = None


class ClipUpdate(BaseModel):
    """Request model for updating a clip."""

    title: str | None = None
    description: str | None = None


class ClipStats(BaseModel):
    """Response model for clip statistics."""

    total_clips: int
    audio_clips: int
    video_clips: int


class BulkClipDeleteRequest(BaseModel):
    """Request model for deleting multiple clips in one call."""

    clip_ids: list[int]


def _clip_output_type(
    source_media: MediaDetails, requested: MediaType | None, *, from_source: bool
) -> MediaType:
    """The type of clip to make, or a 400 explaining why this source can't give it."""
    if not from_source:
        if requested is not None and requested != source_media.media_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'A clip of this media is always {source_media.media_type.value}',
            )
        return source_media.media_type

    if youtube_video_id(source_media.url) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Only YouTube sources can be clipped without a local file',
        )
    if requested not in LIBRARY_MEDIA_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Choose audio or video for a clip fetched from YouTube',
        )
    return requested


@router.get(
    '/stats',
    status_code=status.HTTP_200_OK,
    response_description='Clip statistics',
    response_model=ClipStats,
)
async def get_clip_stats(effective_user_id: int | None = Depends(get_effective_user_id)):
    """Get clip statistics.

    Returns counts for:
    - total_clips: total number of clips
    - audio_clips: number of audio clips
    - video_clips: number of video clips
    """
    stats = await clips_repo.get_clip_stats(user_id=effective_user_id)
    return ClipStats(**stats)


@router.post(
    '',
    status_code=status.HTTP_201_CREATED,
    response_description='Create a new clip',
)
async def create_clip(
    request: Request, clip_request: ClipCreate, user_id: int = Depends(get_required_user_id)
):
    """Create a new clip from a media item.

    Validates the time range and source media, creates a Clip record,
    copies source metadata, and queues a background job to generate the clip file.

    A source the app holds a file of is cut from that file, and the clip takes the
    source's type; `media_type`, if sent, must match it. A source with no file
    (a transcript-only item, or deleted media that kept its transcript) is cut from
    YouTube instead: it must be a YouTube video, and `media_type` (`AUDIO` for an MP3,
    `VIDEO` for an MP4) is required.
    """
    source_media = await get_entity_or_404(
        md_repo.get_media_details_by_id,
        clip_request.media_details_id,
        'Source media',
        access_check=partial(
            ma_repo.check_access_or_raise, user_id, is_admin=request.state.is_admin
        ),
    )

    from_source = not md_repo.has_playable_file(source_media)
    output_type = _clip_output_type(source_media, clip_request.media_type, from_source=from_source)

    if clip_request.start_time < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Start time cannot be negative',
        )

    if clip_request.end_time <= clip_request.start_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='End time must be greater than start time',
        )

    if source_media.duration and clip_request.end_time > source_media.duration:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'End time ({clip_request.end_time}s) exceeds media duration ({source_media.duration}s)',
        )

    task_id = str(uuid.uuid4())

    task_record = TaskRecord(
        task_id=task_id,
        task_type=TaskType.CLIP_GENERATION,
        title=clip_request.title,
        channel=source_media.channel,
        media_type=output_type,
        status=TaskStatus.QUEUED,
        status_message='Waiting to generate clip...',
        user_id=user_id,
    )
    await tr_repo.insert_task(task_record)

    duration = clip_request.end_time - clip_request.start_time
    clip = Clip(
        media_details_id=clip_request.media_details_id,
        title=clip_request.title,
        description=clip_request.description,
        start_time=clip_request.start_time,
        end_time=clip_request.end_time,
        duration=duration,
        media_type=output_type,
        status=TaskStatus.QUEUED,
        task_record_id=task_record.id,
        source_title=source_media.title,
        source_channel=source_media.channel,
        user_id=user_id,
    )
    clip = await clips_repo.add_clip(clip)

    clip_data = {
        'clip_id': clip.id,
        'media_details_id': clip_request.media_details_id,
        'start_time': clip_request.start_time,
        'end_time': clip_request.end_time,
        'media_type': output_type.value,
        'user_id': user_id,
    }
    if from_source:
        clip_data['source_url'] = source_media.url

    seq = await tr_repo.get_next_queue_sequence()
    await tr_repo.update_one(task_id, {'queue_sequence': seq})
    await orch.submit(
        JobSpec(
            job_name=CLIP_FROM_SOURCE_JOB if from_source else CLIP_JOB,
            args=(clip_data,),
            task_id=task_id,
            priority=DIRECT_DOWNLOAD_PRIORITY,
            queue_sequence=seq,
            user_id=user_id,
        )
    )

    publish_status_change(
        task_id, TaskStatus.QUEUED.value, 'Waiting to generate clip...', user_id=user_id
    )

    logger.info(f'Created clip {clip.id} with task {task_id}')

    return {
        'id': clip.id,
        'task_id': task_id,
        'status': TaskStatus.QUEUED.value,
    }


@router.get(
    '',
    status_code=status.HTTP_200_OK,
    response_description='List of clips',
    response_model=dict[str, int | list[dict[str, Any]]],
)
async def get_all_clips(
    search: str | None = None,
    media_type: str | None = None,
    page: int = 1,
    page_size: int | None = None,
    sort_by: str | None = None,
    sort_direction: str = 'desc',
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get all clips with optional filtering and pagination."""
    if page_size is None:
        settings = await settings_repo.get_settings()
        page_size = settings.download_table_page_size

    return await clips_repo.get_all_clips(
        search=search,
        media_type=media_type,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_direction=sort_direction,
        user_id=effective_user_id,
    )


@router.get(
    '/{id}',
    status_code=status.HTTP_200_OK,
    response_description='Clip details',
)
async def get_clip(clip=Depends(get_accessible_clip)):
    """Get a single clip by ID."""
    return clip


@router.get(
    '/media/{media_details_id}',
    status_code=status.HTTP_200_OK,
    response_description='Clips for a media item',
)
async def get_clips_by_media(
    media_details_id: int,
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get all clips created from a specific media item."""
    return await clips_repo.get_clips_by_media_details_id(
        media_details_id, user_id=effective_user_id
    )


@router.patch(
    '/{id}',
    status_code=status.HTTP_200_OK,
    response_description='Updated clip',
)
async def update_clip(
    request: Request, id: int, update: ClipUpdate, user_id: int = Depends(get_required_user_id)
):
    """Update a clip's title or description. Only owner or admin."""
    clip = await get_entity_or_404(
        clips_repo.get_clip_by_id,
        id,
        'Clip',
        access_check=partial(
            ca_repo.check_clip_owner_or_raise, user_id, is_admin=request.state.is_admin
        ),
    )

    update_data = update.model_dump(exclude_unset=True)
    if not update_data:
        return clip

    return await clips_repo.update_clip(id, update_data)


@router.delete(
    '/bulk',
    status_code=status.HTTP_200_OK,
    response_description='Delete multiple clips',
    response_model=dict,
)
async def bulk_delete_clips(
    request: Request,
    bulk_req: BulkClipDeleteRequest,
    user_id: int = Depends(get_required_user_id),
):
    """Delete multiple clips in one call. Owner/admin clips are removed (file + row);
    shared clips just drop the caller's access row. Inaccessible clips are ignored."""
    return await clips_repo.bulk_delete_clips(bulk_req.clip_ids, user_id, request.state.is_admin)


@router.delete(
    '/{id}',
    status_code=status.HTTP_204_NO_CONTENT,
    response_description='Clip deleted successfully',
)
async def delete_clip(
    request: Request,
    id: int,
    user_id: int = Depends(get_required_user_id),
    clip=Depends(get_accessible_clip),
):
    """Delete a clip. Owner/admin = actual delete. Shared user = remove own access."""
    # If user is the owner or admin -> actual delete
    if clip.user_id == user_id or request.state.is_admin:
        deleted_count = await clips_repo.delete_clip_by_id(id)
        if deleted_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f'Clip with id {id} not found',
            )
        return

    # If user has shared access -> remove their access only
    removed = await ca_repo.remove_access(user_id, id)
    if removed:
        return

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f'Clip with id {id} not found',
    )


# --- Sharing endpoints ---


async def _grant_clip(user_ids: list[int], clip_id: int) -> None:
    for uid in user_ids:
        await ca_repo.add_access(uid, clip_id)


register_sharing_routes(
    router,
    SharingConfig(
        entity_name='Clip',
        noun='clip',
        id_key='clip_id',
        get_by_id=clips_repo.get_clip_by_id,
        check_owner_or_raise=ca_repo.check_clip_owner_or_raise,
        grant=_grant_clip,
        revoke=ca_repo.remove_access,
        list_user_ids=ca_repo.get_users_with_access,
    ),
    bulk=False,
)
