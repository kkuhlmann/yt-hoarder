import contextlib
import os
from datetime import timedelta
from typing import Literal, NamedTuple, get_args

from cachetools import TTLCache
from sqlalchemy import and_, case, distinct, func, or_
from sqlmodel import select

from database import db
from models import (
    LIBRARY_MEDIA_TYPES,
    Clip,
    ClipAccess,
    MediaAccess,
    MediaDetails,
    MediaType,
    PlaybackState,
    Playlist,
    PlaylistAccess,
    PlaylistMedia,
    Subscription,
    SubscriptionAccess,
    TaskRecord,
    TaskStatus,
    TaskType,
    TranscriptBlock,
    utc_now,
)

# TTL cache for expensive stats queries. Filesystem scans (overview, storage)
# are the main targets — thousands of os.path.getsize() calls per request.
# Bounded because the key includes the caller-supplied `channel` param: an
# unbounded dict would let any authenticated user grow memory without limit.
STATS_CACHE_TTL_SECONDS = 300  # 5 minutes
_cache: TTLCache[str, dict] = TTLCache(maxsize=1024, ttl=STATS_CACHE_TTL_SECONDS)


def _complete_library_media():
    """Finished audio and video. Transcript-only items aren't library media; they count
    only toward the transcription figures."""
    return and_(
        MediaDetails.status == TaskStatus.COMPLETE,
        MediaDetails.media_type.in_(LIBRARY_MEDIA_TYPES),
    )


def _get_cached(key: str) -> dict | None:
    return _cache.get(key)


def _set_cached(key: str, result: dict) -> None:
    _cache[key] = result


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------


def _make_cache_key(
    base: str,
    channel: str | None = None,
    playlist_id: int | None = None,
    user_id: int | None = None,
) -> str:
    key = base
    if channel:
        key += f':ch={channel}'
    if playlist_id:
        key += f':pl={playlist_id}'
    if user_id is not None:
        key += f':uid={user_id}'
    return key


def _accessible_media_subquery(user_id: int):
    return select(MediaAccess.media_details_id).where(MediaAccess.user_id == user_id)


def _owned_media_condition(user_id: int):
    return MediaDetails.owner_id == user_id


async def _get_playlist_media_ids(session, playlist_id: int) -> list[int]:
    stmt = select(PlaylistMedia.media_details_id).where(PlaylistMedia.playlist_id == playlist_id)
    rows = (await session.execute(stmt)).all()
    return [row[0] for row in rows]


class ResolvedFilters(NamedTuple):
    channel: str | None
    playlist_media_ids: list[int] | None
    accessible_ids: object  # subquery or None
    user_id: int | None

    @property
    def media_kw(self) -> dict:
        """Kwargs for _apply_media_filters."""
        return {
            'channel': self.channel,
            'playlist_media_ids': self.playlist_media_ids,
            'accessible_ids': self.accessible_ids,
        }

    @property
    def owned_kw(self) -> dict:
        """Kwargs for _apply_owned_media_filters."""
        return {
            'channel': self.channel,
            'playlist_media_ids': self.playlist_media_ids,
            'user_id': self.user_id,
        }


async def _resolve_filters(
    session,
    *,
    channel: str | None,
    playlist_id: int | None,
    user_id: int | None,
) -> ResolvedFilters | None:
    """Resolve common filter params into concrete values.

    Returns None when playlist_id resolves to zero media IDs (caller should return empty result).
    """
    playlist_media_ids = None
    if playlist_id:
        playlist_media_ids = await _get_playlist_media_ids(session, playlist_id)
        if not playlist_media_ids:
            return None
    accessible_ids = _accessible_media_subquery(user_id) if user_id is not None else None
    return ResolvedFilters(channel, playlist_media_ids, accessible_ids, user_id)


def _apply_media_filters(stmt, *, channel=None, playlist_media_ids=None, accessible_ids=None):
    if channel:
        stmt = stmt.where(MediaDetails.channel == channel)
    if playlist_media_ids is not None:
        stmt = stmt.where(MediaDetails.id.in_(playlist_media_ids))
    if accessible_ids is not None:
        stmt = stmt.where(MediaDetails.id.in_(accessible_ids))
    return stmt


def _apply_owned_media_filters(stmt, *, channel=None, playlist_media_ids=None, user_id=None):
    """Apply channel/playlist filters + owner_id (for disk/storage queries)."""
    if channel:
        stmt = stmt.where(MediaDetails.channel == channel)
    if playlist_media_ids is not None:
        stmt = stmt.where(MediaDetails.id.in_(playlist_media_ids))
    if user_id is not None:
        stmt = stmt.where(_owned_media_condition(user_id))
    return stmt


def _apply_clip_filters(stmt, *, channel=None, playlist_media_ids=None, clip_user_filter=None):
    if channel:
        stmt = stmt.where(Clip.source_channel == channel)
    if playlist_media_ids is not None:
        stmt = stmt.where(Clip.media_details_id.in_(playlist_media_ids))
    if clip_user_filter is not None:
        stmt = stmt.where(clip_user_filter)
    return stmt


# ---------------------------------------------------------------------------
# Empty-result factories (for early-return on empty playlist)
# ---------------------------------------------------------------------------


def _empty_library_overview() -> dict:
    return {
        'total_media': 0,
        'audio_count': 0,
        'video_count': 0,
        'total_duration_seconds': 0,
        'unique_channels': 0,
        'active_subscriptions': None,
        'transcripts_count': 0,
        'total_disk_bytes': 0,
    }


def _empty_storage_stats() -> dict:
    return {'total_bytes': 0, 'by_type': [], 'by_channel': [], 'largest_files': []}


def _empty_transcription_stats() -> dict:
    return {
        'total_media': 0,
        'with_transcripts': 0,
        'coverage_percent': 0.0,
        'total_blocks': 0,
    }


def _empty_engagement_stats() -> dict:
    return {'most_replayed': [], 'top_channels': []}


def _empty_downloads_over_time(granularity: str) -> dict:
    return {
        'granularity': granularity,
        'periods': [],
        'cumulative': [],
        'by_channel': [],
        'top_channels': [],
    }


def _empty_clips_stats(granularity: str) -> dict:
    return {
        'total_clips': 0,
        'complete_clips': 0,
        'most_clipped_sources': [],
        'over_time': [],
        'granularity': granularity,
    }


def _empty_success_rate(granularity: str) -> dict:
    return {
        'granularity': granularity,
        'periods': [],
        'totals': {'success': 0, 'failed': 0, 'retry': 0, 'total': 0},
        'success_rate': 0.0,
    }


def _empty_heatmap(start_date: str, end_date: str) -> dict:
    return {
        'data': [],
        'max_count': 0,
        'total_days_active': 0,
        'start_date': start_date,
        'end_date': end_date,
    }


# ---------------------------------------------------------------------------
# Library overview query helpers
# ---------------------------------------------------------------------------


async def _query_media_counts(session, f: ResolvedFilters) -> tuple[int, int, int]:
    """Return (total, audio_count, video_count) for complete media."""
    complete = _complete_library_media()

    total_stmt = _apply_media_filters(
        select(func.count()).select_from(MediaDetails).where(complete), **f.media_kw
    )
    total = (await session.execute(total_stmt)).scalar() or 0

    type_stmt = _apply_media_filters(
        select(MediaDetails.media_type, func.count())
        .where(complete)
        .group_by(MediaDetails.media_type),
        **f.media_kw,
    )
    type_rows = (await session.execute(type_stmt)).all()
    audio_count = 0
    video_count = 0
    for media_type, count in type_rows:
        if media_type == MediaType.AUDIO:
            audio_count = count
        elif media_type == MediaType.VIDEO:
            video_count = count
    return total, audio_count, video_count


async def _query_total_duration(session, f: ResolvedFilters) -> float:
    """Return sum of duration for complete media."""
    complete = _complete_library_media()
    stmt = _apply_media_filters(
        select(func.sum(MediaDetails.duration)).where(complete), **f.media_kw
    )
    return (await session.execute(stmt)).scalar() or 0.0


async def _query_unique_channels(session, f: ResolvedFilters) -> int:
    """Return count of distinct channels for complete media."""
    complete = _complete_library_media()
    stmt = _apply_media_filters(
        select(func.count(distinct(MediaDetails.channel)))
        .where(complete)
        .where(MediaDetails.channel.isnot(None)),
        **f.media_kw,
    )
    return (await session.execute(stmt)).scalar() or 0


async def _query_active_subscriptions(
    session, f: ResolvedFilters, playlist_id: int | None
) -> int | None:
    """Return count of active subscriptions, or None for playlist filter."""
    if playlist_id:
        return None
    sub_stmt = select(func.count()).select_from(Subscription).where(Subscription.enabled)
    if f.channel:
        sub_stmt = sub_stmt.where(Subscription.channel == f.channel)
    if f.user_id is not None:
        accessible_sub_ids = select(SubscriptionAccess.subscription_id).where(
            SubscriptionAccess.user_id == f.user_id
        )
        sub_stmt = sub_stmt.where(
            or_(Subscription.user_id == f.user_id, Subscription.id.in_(accessible_sub_ids))
        )
    return (await session.execute(sub_stmt)).scalar() or 0


async def _query_transcripts_count(session, f: ResolvedFilters) -> int:
    trans_stmt = select(func.count(distinct(TranscriptBlock.media_details_id)))
    if f.channel or f.user_id is not None:
        trans_stmt = trans_stmt.join(
            MediaDetails, TranscriptBlock.media_details_id == MediaDetails.id
        )
        if f.channel:
            trans_stmt = trans_stmt.where(MediaDetails.channel == f.channel)
        if f.accessible_ids is not None:
            trans_stmt = trans_stmt.where(MediaDetails.id.in_(f.accessible_ids))
    if f.playlist_media_ids is not None:
        trans_stmt = trans_stmt.where(TranscriptBlock.media_details_id.in_(f.playlist_media_ids))
    return (await session.execute(trans_stmt)).scalar() or 0


async def _query_disk_file_paths(session, f: ResolvedFilters) -> list[str]:
    """Query file paths for disk usage (uses owner_id, not accessible_ids)."""
    stmt = _apply_owned_media_filters(
        select(MediaDetails.file_path).where(
            MediaDetails.status == TaskStatus.COMPLETE,
            MediaDetails.file_path.isnot(None),
        ),
        **f.owned_kw,
    )
    rows = (await session.execute(stmt)).all()
    return [row[0] for row in rows]


def _sum_file_sizes(file_paths: list[str]) -> int:
    """Sum file sizes, ignoring missing files."""
    total = 0
    for path in file_paths:
        with contextlib.suppress(OSError):
            total += os.path.getsize(path)
    return total


# ---------------------------------------------------------------------------
# Downloads-over-time query helpers
# ---------------------------------------------------------------------------


def _build_periods_and_cumulative(bucket_rows, date_fmt: str) -> tuple[list[dict], list[dict]]:
    """Build periods list and cumulative totals from download bucket query results."""
    periods: dict[str, dict] = {}
    for period, media_type, count in bucket_rows:
        key = period.strftime(date_fmt)
        if key not in periods:
            periods[key] = {'period': key, 'audio': 0, 'video': 0}
        mt = media_type.value if hasattr(media_type, 'value') else media_type
        periods[key][mt.lower()] = count

    periods_list = list(periods.values())

    cumulative = []
    running_audio = 0
    running_video = 0
    for entry in periods_list:
        running_audio += entry['audio']
        running_video += entry['video']
        cumulative.append(
            {
                'period': entry['period'],
                'total': running_audio + running_video,
                'audio': running_audio,
                'video': running_video,
            }
        )
    return periods_list, cumulative


async def _query_download_buckets(session, granularity: str, f: ResolvedFilters):
    """Query download counts by media type per time bucket."""
    complete = _complete_library_media()
    bucket = func.date_trunc(granularity, MediaDetails.downloaded_at).label('bucket')
    stmt = (
        _apply_media_filters(
            select(bucket, MediaDetails.media_type, func.count().label('count')).where(
                complete, MediaDetails.downloaded_at.isnot(None)
            ),
            **f.media_kw,
        )
        .group_by('bucket', MediaDetails.media_type)
        .order_by('bucket')
    )
    return (await session.execute(stmt)).all()


async def _query_top_download_channels(session, f: ResolvedFilters) -> list[str]:
    """Return top 5 channels by download count."""
    complete = _complete_library_media()
    stmt = (
        _apply_media_filters(
            select(MediaDetails.channel, func.count().label('cnt')).where(
                complete, MediaDetails.channel.isnot(None)
            ),
            **f.media_kw,
        )
        .group_by(MediaDetails.channel)
        .order_by(func.count().desc())
        .limit(5)
    )
    return [row[0] for row in (await session.execute(stmt)).all()]


async def _query_channel_download_buckets(
    session, granularity: str, top_channels: list[str], f: ResolvedFilters, date_fmt: str
) -> list[dict]:
    if not top_channels:
        return []
    complete = _complete_library_media()
    bucket = func.date_trunc(granularity, MediaDetails.downloaded_at).label('bucket')
    stmt = (
        _apply_media_filters(
            select(bucket, MediaDetails.channel, func.count().label('count')).where(
                complete,
                MediaDetails.downloaded_at.isnot(None),
                MediaDetails.channel.in_(top_channels),
            ),
            **f.media_kw,
        )
        .group_by('bucket', MediaDetails.channel)
        .order_by('bucket')
    )
    rows = (await session.execute(stmt)).all()

    by_channel_periods: dict[str, dict] = {}
    for period, chan_name, count in rows:
        key = period.strftime(date_fmt)
        if key not in by_channel_periods:
            by_channel_periods[key] = {'period': key}
        by_channel_periods[key][chan_name] = count
    return list(by_channel_periods.values())


# ---------------------------------------------------------------------------
# Success-rate query helpers
# ---------------------------------------------------------------------------


async def _resolve_task_record_ids(
    session,
    *,
    user_id: int | None,
    playlist_id: int | None,
) -> tuple[list[int] | None, list[int] | None] | None:
    """Resolve user/playlist to task record ID lists.

    Returns (user_task_record_ids, playlist_task_record_ids), or None if
    either filter resolved to zero IDs (caller should return empty result).
    """
    user_task_record_ids = None
    if user_id is not None:
        accessible_ids = _accessible_media_subquery(user_id)
        stmt = select(MediaDetails.download_task_record_id).where(
            MediaDetails.id.in_(accessible_ids),
            MediaDetails.download_task_record_id.isnot(None),
        )
        rows = (await session.execute(stmt)).all()
        user_task_record_ids = [row[0] for row in rows]
        if not user_task_record_ids:
            return None

    playlist_task_record_ids = None
    if playlist_id:
        playlist_media_ids = await _get_playlist_media_ids(session, playlist_id)
        if not playlist_media_ids:
            return None
        stmt = select(MediaDetails.download_task_record_id).where(
            MediaDetails.id.in_(playlist_media_ids),
            MediaDetails.download_task_record_id.isnot(None),
        )
        rows = (await session.execute(stmt)).all()
        playlist_task_record_ids = [row[0] for row in rows]
        if not playlist_task_record_ids:
            return None

    return user_task_record_ids, playlist_task_record_ids


def _build_success_rate_result(rows, date_fmt: str, granularity: str) -> dict:
    periods: dict[str, dict] = {}
    totals = {'success': 0, 'failed': 0, 'retry': 0}
    for period, bucket, count in rows:
        key = period.strftime(date_fmt)
        if key not in periods:
            periods[key] = {'period': key, 'success': 0, 'failed': 0, 'retry': 0}
        periods[key][bucket] = count
        totals[bucket] = totals.get(bucket, 0) + count

    periods_list = list(periods.values())
    total_all = totals['success'] + totals['failed'] + totals['retry']
    success_rate = round(totals['success'] / total_all * 100, 1) if total_all > 0 else 0.0

    return {
        'granularity': granularity,
        'periods': periods_list,
        'totals': {**totals, 'total': total_all},
        'success_rate': success_rate,
    }


# ===========================================================================
# Public functions
# ===========================================================================


async def get_filter_options(user_id: int | None = None) -> dict:
    """Return distinct channels and playlists that have completed media, with counts."""
    cache_key = _make_cache_key('filter_options', user_id=user_id)
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    async with db.get_async_session() as session:
        complete = _complete_library_media()
        accessible_ids = _accessible_media_subquery(user_id) if user_id is not None else None

        chan_stmt = (
            select(MediaDetails.channel, func.count().label('media_count'))
            .where(complete, MediaDetails.channel.isnot(None))
            .group_by(MediaDetails.channel)
            .order_by(MediaDetails.channel)
        )
        chan_stmt = _apply_media_filters(chan_stmt, accessible_ids=accessible_ids)
        chan_rows = (await session.execute(chan_stmt)).all()
        channels = [{'name': row[0], 'media_count': row[1]} for row in chan_rows]

        # Playlists with at least one completed media member — restricted to
        # playlists the user owns or has PlaylistAccess to (media overlap alone
        # must not leak other users' playlist names).
        pl_stmt = (
            select(
                Playlist.id,
                Playlist.name,
                func.count(PlaylistMedia.media_details_id).label('media_count'),
            )
            .join(PlaylistMedia, Playlist.id == PlaylistMedia.playlist_id)
            .join(MediaDetails, PlaylistMedia.media_details_id == MediaDetails.id)
            .where(complete)
            .group_by(Playlist.id, Playlist.name)
            .order_by(Playlist.name)
        )
        pl_stmt = _apply_media_filters(pl_stmt, accessible_ids=accessible_ids)
        if user_id is not None:
            accessible_playlist_ids = select(PlaylistAccess.playlist_id).where(
                PlaylistAccess.user_id == user_id
            )
            pl_stmt = pl_stmt.where(
                or_(Playlist.user_id == user_id, Playlist.id.in_(accessible_playlist_ids))
            )
        pl_rows = (await session.execute(pl_stmt)).all()
        playlists = [{'id': row[0], 'name': row[1], 'media_count': row[2]} for row in pl_rows]

    result = {'channels': channels, 'playlists': playlists}
    _set_cached(cache_key, result)
    return result


async def get_library_overview(
    channel: str | None = None,
    playlist_id: int | None = None,
    user_id: int | None = None,
) -> dict:
    """Get high-level library stats: totals, duration, channels, subscriptions, transcripts, disk."""
    cache_key = _make_cache_key('library_overview', channel, playlist_id, user_id)
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    async with db.get_async_session() as session:
        f = await _resolve_filters(
            session, channel=channel, playlist_id=playlist_id, user_id=user_id
        )
        if f is None:
            result = _empty_library_overview()
            _set_cached(cache_key, result)
            return result

        total, audio_count, video_count = await _query_media_counts(session, f)
        total_duration = await _query_total_duration(session, f)
        unique_channels = await _query_unique_channels(session, f)
        active_subs = await _query_active_subscriptions(session, f, playlist_id)
        transcripts_count = await _query_transcripts_count(session, f)
        file_paths = await _query_disk_file_paths(session, f)

    result = {
        'total_media': total,
        'audio_count': audio_count,
        'video_count': video_count,
        'total_duration_seconds': total_duration,
        'unique_channels': unique_channels,
        'active_subscriptions': active_subs,
        'transcripts_count': transcripts_count,
        'total_disk_bytes': _sum_file_sizes(file_paths),
    }
    _set_cached(cache_key, result)
    return result


def _compute_file_sizes(rows) -> list[dict]:
    """Compute on-disk sizes for DB rows, skipping missing files."""
    file_data = []
    for row_id, file_path, media_type, chan, title in rows:
        try:
            size = os.path.getsize(file_path)
        except OSError:
            continue
        file_data.append(
            {
                'id': row_id,
                'file_path': file_path,
                'media_type': media_type.value if hasattr(media_type, 'value') else media_type,
                'channel': chan or 'Unknown',
                'title': title or 'Untitled',
                'size_bytes': size,
            }
        )
    return file_data


def _aggregate_by_type_and_channel(file_data: list[dict]) -> tuple[list[dict], list[dict]]:
    """Aggregate file sizes by media type and by channel (top 10)."""
    by_type: dict[str, int] = {}
    by_channel: dict[str, int] = {}
    for item in file_data:
        by_type[item['media_type']] = by_type.get(item['media_type'], 0) + item['size_bytes']
        by_channel[item['channel']] = by_channel.get(item['channel'], 0) + item['size_bytes']

    by_type_list = [{'media_type': k, 'size_bytes': v} for k, v in by_type.items()]
    by_channel_sorted = sorted(by_channel.items(), key=lambda x: x[1], reverse=True)[:10]
    by_channel_list = [{'channel': ch, 'size_bytes': sz} for ch, sz in by_channel_sorted]
    return by_type_list, by_channel_list


async def get_storage_stats(
    channel: str | None = None,
    playlist_id: int | None = None,
    user_id: int | None = None,
) -> dict:
    """Get storage usage by type and channel, plus largest files.

    Computes file sizes on-demand via os.path.getsize() for complete media with file paths.
    """
    cache_key = _make_cache_key('storage_stats', channel, playlist_id, user_id)
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    async with db.get_async_session() as session:
        f = await _resolve_filters(
            session, channel=channel, playlist_id=playlist_id, user_id=user_id
        )
        if f is None:
            result = _empty_storage_stats()
            _set_cached(cache_key, result)
            return result

        stmt = _apply_owned_media_filters(
            select(
                MediaDetails.id,
                MediaDetails.file_path,
                MediaDetails.media_type,
                MediaDetails.channel,
                MediaDetails.title,
            ).where(
                MediaDetails.status == TaskStatus.COMPLETE,
                MediaDetails.file_path.isnot(None),
            ),
            **f.owned_kw,
        )
        rows = (await session.execute(stmt)).all()

    file_data = _compute_file_sizes(rows)
    by_type_list, by_channel_list = _aggregate_by_type_and_channel(file_data)

    largest = sorted(file_data, key=lambda x: x['size_bytes'], reverse=True)[:20]
    largest_files = [
        {k: item[k] for k in ('id', 'title', 'channel', 'media_type', 'size_bytes')}
        for item in largest
    ]

    result = {
        'total_bytes': sum(item['size_bytes'] for item in file_data),
        'by_type': by_type_list,
        'by_channel': by_channel_list,
        'largest_files': largest_files,
    }
    _set_cached(cache_key, result)
    return result


Granularity = Literal['day', 'week', 'month']
VALID_GRANULARITIES = get_args(Granularity)

# strftime format for each granularity's bucket key
_GRANULARITY_FORMAT = {
    'day': '%Y-%m-%d',
    'week': '%Y-%m-%d',  # DATE_TRUNC('week', ...) returns Monday of that week
    'month': '%Y-%m',
}


async def get_downloads_over_time(
    granularity: str = 'month',
    channel: str | None = None,
    playlist_id: int | None = None,
    user_id: int | None = None,
) -> dict:
    """Get download counts split by media type, grouped by day/week/month."""
    if granularity not in VALID_GRANULARITIES:
        granularity = 'month'

    cache_key = _make_cache_key(f'downloads_over_time:{granularity}', channel, playlist_id, user_id)
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    date_fmt = _GRANULARITY_FORMAT[granularity]

    async with db.get_async_session() as session:
        f = await _resolve_filters(
            session, channel=channel, playlist_id=playlist_id, user_id=user_id
        )
        if f is None:
            result = _empty_downloads_over_time(granularity)
            _set_cached(cache_key, result)
            return result

        bucket_rows = await _query_download_buckets(session, granularity, f)
        periods_list, cumulative = _build_periods_and_cumulative(bucket_rows, date_fmt)
        top_channels = await _query_top_download_channels(session, f)
        by_channel_list = await _query_channel_download_buckets(
            session, granularity, top_channels, f, date_fmt
        )

    result = {
        'granularity': granularity,
        'periods': periods_list,
        'cumulative': cumulative,
        'by_channel': by_channel_list,
        'top_channels': top_channels,
    }
    _set_cached(cache_key, result)
    return result


async def get_transcription_stats(
    channel: str | None = None,
    playlist_id: int | None = None,
    user_id: int | None = None,
) -> dict:
    """Get transcription coverage and block statistics."""
    cache_key = _make_cache_key('transcription_stats', channel, playlist_id, user_id)
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    async with db.get_async_session() as session:
        f = await _resolve_filters(
            session, channel=channel, playlist_id=playlist_id, user_id=user_id
        )
        if f is None:
            result = _empty_transcription_stats()
            _set_cached(cache_key, result)
            return result

        complete = MediaDetails.status == TaskStatus.COMPLETE

        total_stmt = _apply_media_filters(
            select(func.count()).select_from(MediaDetails).where(complete), **f.media_kw
        )
        total_media = (await session.execute(total_stmt)).scalar() or 0

        # Conditional join when channel or user filter active
        with_trans_stmt = select(func.count(distinct(TranscriptBlock.media_details_id)))
        if f.channel or f.user_id is not None:
            with_trans_stmt = with_trans_stmt.join(
                MediaDetails, TranscriptBlock.media_details_id == MediaDetails.id
            )
            if f.channel:
                with_trans_stmt = with_trans_stmt.where(MediaDetails.channel == f.channel)
            if f.accessible_ids is not None:
                with_trans_stmt = with_trans_stmt.where(MediaDetails.id.in_(f.accessible_ids))
        if f.playlist_media_ids is not None:
            with_trans_stmt = with_trans_stmt.where(
                TranscriptBlock.media_details_id.in_(f.playlist_media_ids)
            )
        with_transcripts = (await session.execute(with_trans_stmt)).scalar() or 0

        blocks_stmt = select(func.count()).select_from(TranscriptBlock)
        if f.channel or f.user_id is not None:
            blocks_stmt = blocks_stmt.join(
                MediaDetails, TranscriptBlock.media_details_id == MediaDetails.id
            )
            if f.channel:
                blocks_stmt = blocks_stmt.where(MediaDetails.channel == f.channel)
            if f.accessible_ids is not None:
                blocks_stmt = blocks_stmt.where(MediaDetails.id.in_(f.accessible_ids))
        if f.playlist_media_ids is not None:
            blocks_stmt = blocks_stmt.where(
                TranscriptBlock.media_details_id.in_(f.playlist_media_ids)
            )
        total_blocks = (await session.execute(blocks_stmt)).scalar() or 0

        coverage_pct = (with_transcripts / total_media * 100) if total_media > 0 else 0.0

    result = {
        'total_media': total_media,
        'with_transcripts': with_transcripts,
        'coverage_percent': round(coverage_pct, 1),
        'total_blocks': total_blocks,
    }
    _set_cached(cache_key, result)
    return result


async def _query_most_replayed(session, f: ResolvedFilters) -> list[dict]:
    """Top 15 media by total access_count across PlaybackState."""
    total_access = func.sum(PlaybackState.access_count).label('total_access')
    stmt = (
        select(
            MediaDetails.id,
            MediaDetails.title,
            MediaDetails.channel,
            MediaDetails.media_type,
            total_access,
            MediaDetails.duration,
        )
        .join(PlaybackState, MediaDetails.id == PlaybackState.media_details_id)
        .where(MediaDetails.status == TaskStatus.COMPLETE)
        .group_by(MediaDetails.id)
        .having(func.sum(PlaybackState.access_count) > 0)
        .order_by(total_access.desc())
        .limit(15)
    )
    if f.user_id is not None:
        stmt = stmt.where(PlaybackState.user_id == f.user_id)
    stmt = _apply_media_filters(stmt, **f.media_kw)
    rows = (await session.execute(stmt)).all()
    return [
        {
            'id': r[0],
            'title': r[1],
            'channel': r[2],
            'media_type': r[3].value if hasattr(r[3], 'value') else r[3],
            'access_count': int(r[4]),
            'duration': r[5],
        }
        for r in rows
    ]


async def _query_top_channels(session, f: ResolvedFilters) -> list[dict]:
    """Top 15 channels by total play count across PlaybackState."""
    stmt = (
        select(
            MediaDetails.channel,
            func.sum(PlaybackState.access_count).label('total_plays'),
        )
        .join(PlaybackState, MediaDetails.id == PlaybackState.media_details_id)
        .where(MediaDetails.status == TaskStatus.COMPLETE, MediaDetails.channel.isnot(None))
        .group_by(MediaDetails.channel)
        .having(func.sum(PlaybackState.access_count) > 0)
        .order_by(func.sum(PlaybackState.access_count).desc())
        .limit(15)
    )
    if f.user_id is not None:
        stmt = stmt.where(PlaybackState.user_id == f.user_id)
    stmt = _apply_media_filters(stmt, **f.media_kw)
    rows = (await session.execute(stmt)).all()
    return [{'channel': r[0], 'total_plays': int(r[1])} for r in rows]


async def get_engagement_stats(
    channel: str | None = None,
    playlist_id: int | None = None,
    user_id: int | None = None,
) -> dict:
    """Get engagement data: most replayed media and top channels by play count."""
    cache_key = _make_cache_key('engagement_stats', channel, playlist_id, user_id)
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    async with db.get_async_session() as session:
        f = await _resolve_filters(
            session, channel=channel, playlist_id=playlist_id, user_id=user_id
        )
        if f is None:
            result = _empty_engagement_stats()
            _set_cached(cache_key, result)
            return result

        most_replayed = await _query_most_replayed(session, f)
        top_channels = await _query_top_channels(session, f)

    result = {'most_replayed': most_replayed, 'top_channels': top_channels}
    _set_cached(cache_key, result)
    return result


async def get_clips_stats(
    granularity: str = 'month',
    channel: str | None = None,
    playlist_id: int | None = None,
    user_id: int | None = None,
) -> dict:
    """Get clips statistics: total count, most clipped sources, and clips over time."""
    if granularity not in VALID_GRANULARITIES:
        granularity = 'month'

    cache_key = _make_cache_key(f'clips_stats:{granularity}', channel, playlist_id, user_id)
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    date_fmt = _GRANULARITY_FORMAT[granularity]

    async with db.get_async_session() as session:
        playlist_media_ids = None
        if playlist_id:
            playlist_media_ids = await _get_playlist_media_ids(session, playlist_id)
            if not playlist_media_ids:
                result = _empty_clips_stats(granularity)
                _set_cached(cache_key, result)
                return result

        # Build clip user filter (uses ClipAccess, not MediaAccess)
        clip_user_filter = None
        if user_id is not None:
            accessible_clip_ids = select(ClipAccess.clip_id).where(ClipAccess.user_id == user_id)
            clip_user_filter = or_(Clip.user_id == user_id, Clip.id.in_(accessible_clip_ids))

        cf_kw = {
            'channel': channel,
            'playlist_media_ids': playlist_media_ids,
            'clip_user_filter': clip_user_filter,
        }

        total_stmt = _apply_clip_filters(select(func.count()).select_from(Clip), **cf_kw)
        total_clips = (await session.execute(total_stmt)).scalar() or 0

        complete_stmt = _apply_clip_filters(
            select(func.count()).select_from(Clip).where(Clip.status == TaskStatus.COMPLETE),
            **cf_kw,
        )
        complete_clips = (await session.execute(complete_stmt)).scalar() or 0

        sources_stmt = (
            _apply_clip_filters(
                select(
                    Clip.source_title,
                    Clip.source_channel,
                    func.count().label('clip_count'),
                ).where(Clip.source_title.isnot(None)),
                **cf_kw,
            )
            .group_by(Clip.source_title, Clip.source_channel)
            .order_by(func.count().desc())
            .limit(10)
        )
        source_rows = (await session.execute(sources_stmt)).all()
        most_clipped_sources = [
            {
                'title': r[0] or 'Untitled',
                'channel': r[1] or 'Unknown',
                'clip_count': r[2],
            }
            for r in source_rows
        ]

        bucket = func.date_trunc(granularity, Clip.created_at).label('bucket')
        time_stmt = (
            _apply_clip_filters(select(bucket, func.count().label('count')), **cf_kw)
            .group_by('bucket')
            .order_by('bucket')
        )
        time_rows = (await session.execute(time_stmt)).all()
        over_time = [{'period': row[0].strftime(date_fmt), 'count': row[1]} for row in time_rows]

    result = {
        'total_clips': total_clips,
        'complete_clips': complete_clips,
        'most_clipped_sources': most_clipped_sources,
        'over_time': over_time,
        'granularity': granularity,
    }
    _set_cached(cache_key, result)
    return result


async def get_download_success_rate(
    granularity: str = 'month',
    channel: str | None = None,
    playlist_id: int | None = None,
    user_id: int | None = None,
) -> dict:
    """Get download task success/failure/retry counts grouped by time period."""
    if granularity not in VALID_GRANULARITIES:
        granularity = 'month'

    cache_key = _make_cache_key(
        f'download_success_rate:{granularity}', channel, playlist_id, user_id
    )
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    date_fmt = _GRANULARITY_FORMAT[granularity]

    status_bucket = case(
        (TaskRecord.status == TaskStatus.COMPLETE, 'success'),
        (TaskRecord.status.in_([TaskStatus.FAILED, TaskStatus.UPSTREAM_FAILED]), 'failed'),
        (TaskRecord.status == TaskStatus.RETRY, 'retry'),
        else_='other',
    ).label('bucket')

    async with db.get_async_session() as session:
        period_col = func.date_trunc(granularity, TaskRecord.created_at).label('period')

        resolved = await _resolve_task_record_ids(session, user_id=user_id, playlist_id=playlist_id)
        if resolved is None:
            result = _empty_success_rate(granularity)
            _set_cached(cache_key, result)
            return result

        user_task_record_ids, playlist_task_record_ids = resolved

        stmt = (
            select(period_col, status_bucket, func.count().label('count'))
            .where(
                TaskRecord.task_type == TaskType.DOWNLOAD,
                TaskRecord.status.in_(
                    [
                        TaskStatus.COMPLETE,
                        TaskStatus.FAILED,
                        TaskStatus.UPSTREAM_FAILED,
                        TaskStatus.RETRY,
                    ]
                ),
            )
            .group_by('period', 'bucket')
            .order_by('period')
        )
        if channel:
            stmt = stmt.where(TaskRecord.channel == channel)
        if playlist_task_record_ids is not None:
            stmt = stmt.where(TaskRecord.id.in_(playlist_task_record_ids))
        if user_task_record_ids is not None:
            stmt = stmt.where(TaskRecord.id.in_(user_task_record_ids))
        rows = (await session.execute(stmt)).all()

    result = _build_success_rate_result(rows, date_fmt, granularity)
    _set_cached(cache_key, result)
    return result


async def get_download_activity_heatmap(
    channel: str | None = None,
    playlist_id: int | None = None,
    user_id: int | None = None,
) -> dict:
    """Get daily download counts for the last 365 days (calendar heatmap data)."""
    cache_key = _make_cache_key('download_activity_heatmap', channel, playlist_id, user_id)
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    now = utc_now()
    start = now - timedelta(days=365)

    async with db.get_async_session() as session:
        f = await _resolve_filters(
            session, channel=channel, playlist_id=playlist_id, user_id=user_id
        )
        if f is None:
            result = _empty_heatmap(start.strftime('%Y-%m-%d'), now.strftime('%Y-%m-%d'))
            _set_cached(cache_key, result)
            return result

        day_col = func.date_trunc('day', MediaDetails.downloaded_at).label('day')
        stmt = (
            _apply_media_filters(
                select(day_col, func.count().label('count')).where(
                    _complete_library_media(),
                    MediaDetails.downloaded_at.isnot(None),
                    MediaDetails.downloaded_at >= start,
                ),
                **f.media_kw,
            )
            .group_by('day')
            .order_by('day')
        )
        rows = (await session.execute(stmt)).all()

    data = [{'date': row[0].strftime('%Y-%m-%d'), 'count': row[1]} for row in rows]
    max_count = max((d['count'] for d in data), default=0)
    total_days_active = len(data)

    result = {
        'data': data,
        'max_count': max_count,
        'total_days_active': total_days_active,
        'start_date': start.strftime('%Y-%m-%d'),
        'end_date': now.strftime('%Y-%m-%d'),
    }
    _set_cached(cache_key, result)
    return result
