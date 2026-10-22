from functools import partial

from fastapi import APIRouter, Depends, Query, Request, status

from dependencies import get_effective_user_id, get_entity_or_404, get_required_user_id
from repositories import playlist_access as pa_repo
from repositories import playlists as playlists_repo
from repositories import stats as stats_repo
from repositories.stats import Granularity

router = APIRouter()


async def checked_playlist_id(
    request: Request,
    playlist_id: int | None = Query(default=None),
    user_id: int = Depends(get_required_user_id),
) -> int | None:
    """404 unless the caller can access the playlist used as a stats filter."""
    if playlist_id is not None:
        await get_entity_or_404(
            playlists_repo.get_playlist_by_id,
            playlist_id,
            'Playlist',
            access_check=partial(
                pa_repo.check_playlist_access_or_raise, user_id, is_admin=request.state.is_admin
            ),
        )
    return playlist_id


@router.get('/filter-options', status_code=status.HTTP_200_OK)
async def get_filter_options(
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get available channels and playlists for stats filtering."""
    return await stats_repo.get_filter_options(user_id=effective_user_id)


@router.get('/overview', status_code=status.HTTP_200_OK)
async def get_library_overview(
    channel: str | None = Query(default=None),
    playlist_id: int | None = Depends(checked_playlist_id),
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get high-level library statistics."""
    return await stats_repo.get_library_overview(
        channel=channel, playlist_id=playlist_id, user_id=effective_user_id
    )


@router.get('/storage', status_code=status.HTTP_200_OK)
async def get_storage_stats(
    channel: str | None = Query(default=None),
    playlist_id: int | None = Depends(checked_playlist_id),
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get storage usage by type, channel, and largest files."""
    return await stats_repo.get_storage_stats(
        channel=channel, playlist_id=playlist_id, user_id=effective_user_id
    )


@router.get('/downloads-over-time', status_code=status.HTTP_200_OK)
async def get_downloads_over_time(
    granularity: Granularity = Query(default='month'),
    channel: str | None = Query(default=None),
    playlist_id: int | None = Depends(checked_playlist_id),
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get download trends by type and channel, grouped by day/week/month."""
    return await stats_repo.get_downloads_over_time(
        granularity=granularity,
        channel=channel,
        playlist_id=playlist_id,
        user_id=effective_user_id,
    )


@router.get('/transcription', status_code=status.HTTP_200_OK)
async def get_transcription_stats(
    channel: str | None = Query(default=None),
    playlist_id: int | None = Depends(checked_playlist_id),
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get transcription coverage and block statistics."""
    return await stats_repo.get_transcription_stats(
        channel=channel, playlist_id=playlist_id, user_id=effective_user_id
    )


@router.get('/transcript-storage', status_code=status.HTTP_200_OK)
async def get_transcript_storage_stats(
    channel: str | None = Query(default=None),
    playlist_id: int | None = Depends(checked_playlist_id),
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get the database space transcripts take, by channel.

    Sizes are the real on-disk footprint, indexes included: `text_bytes` covers the
    transcript text and its keyword-search index, `embedding_bytes` the embeddings and
    their vector index. Each channel gets its proportional share, so `by_channel` adds up
    to `total_bytes`. `database_bytes`, the size of the whole database, is set only in the
    admin all-users view. Figures are cached, so they can trail recent transcripts by
    several minutes.
    """
    return await stats_repo.get_transcript_storage_stats(
        channel=channel, playlist_id=playlist_id, user_id=effective_user_id
    )


@router.get('/engagement', status_code=status.HTTP_200_OK)
async def get_engagement_stats(
    channel: str | None = Query(default=None),
    playlist_id: int | None = Depends(checked_playlist_id),
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get engagement data: most replayed and top channels."""
    return await stats_repo.get_engagement_stats(
        channel=channel, playlist_id=playlist_id, user_id=effective_user_id
    )


@router.get('/clips', status_code=status.HTTP_200_OK)
async def get_clips_stats(
    granularity: Granularity = Query(default='month'),
    channel: str | None = Query(default=None),
    playlist_id: int | None = Depends(checked_playlist_id),
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get clips statistics: totals, most clipped sources, clips over time."""
    return await stats_repo.get_clips_stats(
        granularity=granularity,
        channel=channel,
        playlist_id=playlist_id,
        user_id=effective_user_id,
    )


@router.get('/download-success-rate', status_code=status.HTTP_200_OK)
async def get_download_success_rate(
    granularity: Granularity = Query(default='month'),
    channel: str | None = Query(default=None),
    playlist_id: int | None = Depends(checked_playlist_id),
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get download task success/failure/retry counts grouped by time period."""
    return await stats_repo.get_download_success_rate(
        granularity=granularity,
        channel=channel,
        playlist_id=playlist_id,
        user_id=effective_user_id,
    )


@router.get('/download-activity-heatmap', status_code=status.HTTP_200_OK)
async def get_download_activity_heatmap(
    channel: str | None = Query(default=None),
    playlist_id: int | None = Depends(checked_playlist_id),
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get daily download counts for the last 365 days (calendar heatmap data)."""
    return await stats_repo.get_download_activity_heatmap(
        channel=channel, playlist_id=playlist_id, user_id=effective_user_id
    )
