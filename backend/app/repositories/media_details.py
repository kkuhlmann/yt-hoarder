import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, asc, case, delete, desc, exists, func, or_
from sqlalchemy import select as sa_select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import InstrumentedAttribute, aliased, joinedload
from sqlmodel import select

from database import db
from logger import logger
from models import (
    MediaAccess,
    MediaDetails,
    MediaRating,
    MediaTag,
    MediaType,
    PlaybackState,
    Tag,
    TaskStatus,
    TranscriptBlock,
    utc_now,
)
from repositories.pagination import page_count
from services.cleanup import delete_file
from ytdlp.files import delete_thumbnail, kept_thumbnail_path, move_thumbnail

# Chunk size for url IN (...) lookups — a whole-channel enumeration can be thousands
# of URLs, well past Postgres' bind-parameter ceiling.
_URL_BATCH_SIZE = 1000

# Sentinels for group buckets that have no natural key
UNKNOWN_CHANNEL = 'Unknown channel'
UNTAGGED_KEY = 'untagged'
UNTAGGED_LABEL = 'Untagged'

# group_by date dimension -> MediaDetails column
_GROUP_DATE_FIELDS = {
    'downloaded': MediaDetails.downloaded_at,
    'released': MediaDetails.release_timestamp,
}

# Fields the upsert functions copy onto an existing row (None values are skipped).
_UPSERT_FIELDS = [
    'channel',
    'title',
    'playlist_index',
    'file_path',
    'file_size_bytes',
    'summary',
    'release_timestamp',
    'duration',
    'thumbnail_path',
    'status',
    'download_task_record_id',
    'transcript_task_record_id',
]


def _copy_upsert_fields(source: MediaDetails, target: MediaDetails) -> None:
    """Copy non-None upsert fields from source onto target.

    `status` gets special treatment: TaskStatus.NONE is the model default and means
    "not provided" — copying it would clobber a real status (e.g. COMPLETE) written
    by a concurrent chain between our existence check and this update.
    """
    for key in _UPSERT_FIELDS:
        value = getattr(source, key, None)
        if value is None:
            continue
        if key == 'status' and value == TaskStatus.NONE:
            continue
        setattr(target, key, value)


# --- Shared helpers ---


def _build_search_condition(
    search: str,
    columns: Sequence[InstrumentedAttribute] = (MediaDetails.channel, MediaDetails.title),
):
    """Parse a search string into a match clause over `columns` (channel/title by default).

    Splits on '||' (OR) then '&&' (AND); '&&' binds tighter than '||'. Each term
    is a case-insensitive substring match against any one of `columns`. Single '&' /
    '|' are treated as literal characters (only the doubled forms are operators),
    so titles like 'Law & Order' keep matching literally. A single-term search
    collapses to exactly the previous behavior. Returns None when no non-empty
    terms survive (e.g. search is just '&&', '||', or whitespace).
    """
    or_groups = []
    for raw_group in search.split('||'):
        term_clauses = [
            or_(*(column.ilike(f'%{term}%') for column in columns))
            for term in (t.strip() for t in raw_group.split('&&'))
            if term
        ]
        if term_clauses:
            or_groups.append(and_(*term_clauses))
    if not or_groups:
        return None
    return or_(*or_groups)


def _build_access_condition(user_id: int, status: str | None, include_owned: bool):
    """Scope a media query to what one user may see."""
    if status in (TaskStatus.DELETED.value, TaskStatus.SKIPPED.value):
        # DELETED records have no MediaAccess rows (removed during soft delete).
        # SKIPPED records should only show the owner's own skipped media.
        return MediaDetails.owner_id == user_id

    accessible_ids = select(MediaAccess.media_details_id).where(MediaAccess.user_id == user_id)
    if include_owned:
        return or_(MediaDetails.id.in_(accessible_ids), MediaDetails.owner_id == user_id)
    return MediaDetails.id.in_(accessible_ids)


def _accessible_to(user_id: int, media):
    """`media` (MediaDetails or an alias of it) is the user's own or shared with them."""
    shared = select(MediaAccess.media_details_id).where(MediaAccess.user_id == user_id)
    return or_(media.owner_id == user_id, media.id.in_(shared))


def _not_superseded_condition(user_id: int):
    """Hide a transcript-only row once the user can search the same URL's full media.

    A full row counts only when it has transcript blocks, so a downloaded video still
    awaiting its transcript leaves the transcript-only entry visible meanwhile, and one
    deleted without keeping its transcript brings the entry back. Decided per viewer,
    since the full media may be shared with one user and not another.
    """
    full = aliased(MediaDetails)
    superseded = exists().where(
        and_(
            full.url == MediaDetails.url,
            full.media_type != MediaType.TRANSCRIPT,
            _accessible_to(user_id, full),
            exists().where(TranscriptBlock.media_details_id == full.id),
        )
    )
    return or_(MediaDetails.media_type != MediaType.TRANSCRIPT, ~superseded)


def _build_media_conditions(
    *,
    user_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    tag_ids: list[int] | None = None,
    min_rating: int | None = None,
    rating_user_id: int | None = None,
    channel: str | None = None,
    untagged: bool = False,
    date_field: str | None = None,
    date_year: int | None = None,
    date_month: int | None = None,
    include_owned: bool = False,
    channels: list[str] | None = None,
    released_from: datetime | None = None,
    released_to: datetime | None = None,
    title_contains: str | None = None,
    media_types: Sequence[MediaType] | None = None,
    has_transcript: bool = False,
) -> list:
    """Build WHERE conditions shared by get_all_media_details and get_media_stats.

    Only adds a status condition when status is truthy. The channel / untagged /
    date_field+date_year(+date_month) params support drilling into a group folder
    (see get_media_groups) by reusing this single list query path.

    include_owned widens the access branch to "shared with me OR mine", stating the
    Owner -> Shared -> Admin tier directly instead of inferring it from status. The
    transcript search needs it: it spans every status, so it never reaches the
    owner_id branch below, and a soft delete removes every MediaAccess row for the
    media including the owner's.

    channels / released_from / released_to narrow by any of several channels and by an
    upload-date range (released_to exclusive), for MCP transcript search. An empty
    channels list matches nothing rather than everything: a caller whose channel names
    all failed to resolve must not silently get the whole library back.

    title_contains is `search` restricted to the title: the same `&&` / `||` syntax, but
    never matching the channel name.

    media_types limits the result to those types; the library passes LIBRARY_MEDIA_TYPES,
    while the transcript search passes none so transcript-only rows stay searchable.
    has_transcript keeps only rows with transcript blocks (the Transcripts view).

    A user-scoped query always drops transcript-only rows the user has superseded with
    the full media (_not_superseded_condition). The admin all-users view (user_id None)
    shows every row, duplicates included.
    """
    conditions = []

    if status:
        conditions.append(MediaDetails.status == status)

    conditions.extend(_type_and_transcript_conditions(media_types, has_transcript))

    if user_id is not None:
        conditions.append(_build_access_condition(user_id, status, include_owned))
        conditions.append(_not_superseded_condition(user_id))

    if search:
        search_condition = _build_search_condition(search)
        if search_condition is not None:
            conditions.append(search_condition)

    if tag_ids and rating_user_id is not None:
        tagged_media_ids = (
            select(MediaTag.media_details_id)
            .where(and_(MediaTag.user_id == rating_user_id, MediaTag.tag_id.in_(tag_ids)))
            .distinct()
        )
        conditions.append(MediaDetails.id.in_(tagged_media_ids))

    if min_rating is not None and rating_user_id is not None:
        rated_media_ids = select(MediaRating.media_details_id).where(
            and_(
                MediaRating.user_id == rating_user_id,
                MediaRating.rating >= min_rating,
            )
        )
        conditions.append(MediaDetails.id.in_(rated_media_ids))

    # --- Group-folder drill-down filters ---

    if channel is not None:
        # Matches the coalesced key used in get_media_groups so the
        # "Unknown channel" bucket (NULL channel) drills in correctly.
        conditions.append(func.coalesce(MediaDetails.channel, UNKNOWN_CHANNEL) == channel)

    if untagged and rating_user_id is not None:
        conditions.append(
            ~MediaDetails.id.in_(
                select(MediaTag.media_details_id).where(MediaTag.user_id == rating_user_id)
            )
        )

    if date_field in _GROUP_DATE_FIELDS and date_year:
        field = _GROUP_DATE_FIELDS[date_field]
        if date_month:
            start = datetime(date_year, date_month, 1)
            end = (
                datetime(date_year + 1, 1, 1)
                if date_month == 12
                else datetime(date_year, date_month + 1, 1)
            )
        else:
            start = datetime(date_year, 1, 1)
            end = datetime(date_year + 1, 1, 1)
        conditions.append(and_(field >= start, field < end))

    conditions.extend(_channel_and_release_conditions(channels, released_from, released_to))
    conditions.extend(_title_conditions(title_contains))
    return conditions


def _title_conditions(title_contains: str | None) -> list:
    if not title_contains:
        return []
    condition = _build_search_condition(title_contains, columns=(MediaDetails.title,))
    return [] if condition is None else [condition]


def _type_and_transcript_conditions(
    media_types: Sequence[MediaType] | None, has_transcript: bool
) -> list:
    conditions = []
    if media_types is not None:
        conditions.append(MediaDetails.media_type.in_(media_types))
    if has_transcript:
        conditions.append(exists().where(TranscriptBlock.media_details_id == MediaDetails.id))
    return conditions


def _channel_and_release_conditions(
    channels: list[str] | None, released_from: datetime | None, released_to: datetime | None
) -> list:
    conditions = []
    if channels is not None:
        conditions.append(func.coalesce(MediaDetails.channel, UNKNOWN_CHANNEL).in_(channels))
    if released_from is not None:
        conditions.append(MediaDetails.release_timestamp >= released_from)
    if released_to is not None:
        conditions.append(MediaDetails.release_timestamp < released_to)
    return conditions


def build_media_scope_subquery(**filters) -> tuple[str, dict] | None:
    """Render the media-list filter as a SQL subquery plus its bound params.

    The transcript search is raw SQL — transcript_embeddings has no ORM model — so
    this is how it reuses the exact conditions the list uses instead of re-deriving
    them in a second dialect. Returns None when nothing is being filtered, which
    keeps the search's SQL byte-identical to its unscoped form.

    render_postcompile is required: without it the tag_ids IN (...) expanding
    bindparam renders as a [POSTCOMPILE_...] token no driver can bind. literal_binds
    would be an injection hole — the search string is user input.
    """
    conditions = _build_media_conditions(**filters)
    if not conditions:
        return None
    stmt = sa_select(MediaDetails.id).where(and_(*conditions))
    compiled = stmt.compile(
        dialect=postgresql.dialect(paramstyle='named'),
        compile_kwargs={'render_postcompile': True},
    )
    return str(compiled), dict(compiled.params)


async def count_scoped_media(**filters) -> int:
    """Count the media the same filters select. Drives the search's query-shape choice."""
    conditions = _build_media_conditions(**filters)
    stmt = sa_select(func.count()).select_from(MediaDetails)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    async with db.get_async_session() as session:
        result = await session.execute(stmt)
        return result.scalar_one()


@dataclass(frozen=True)
class TranscribedChannel:
    channel: str
    video_count: int
    undated_count: int
    first_release: datetime | None
    last_release: datetime | None


def _transcribed_media_conditions(user_id: int) -> list:
    """Media the user can see that have at least one transcript block.

    include_owned for the same reason transcript search passes it: a soft delete
    drops the owner's MediaAccess row, but they keep the transcript.
    """
    conditions = _build_media_conditions(user_id=user_id, include_owned=True)
    conditions.append(MediaDetails.id.in_(sa_select(TranscriptBlock.media_details_id)))
    return conditions


async def get_transcribed_channels(user_id: int) -> list[TranscribedChannel]:
    """Per-channel counts and upload-date span of the user's transcribed media, largest first."""
    channel_key = func.coalesce(MediaDetails.channel, UNKNOWN_CHANNEL)
    stmt = (
        sa_select(
            channel_key,
            func.count(),
            func.count().filter(MediaDetails.release_timestamp.is_(None)),
            func.min(MediaDetails.release_timestamp),
            func.max(MediaDetails.release_timestamp),
        )
        .where(and_(*_transcribed_media_conditions(user_id)))
        .group_by(channel_key)
        .order_by(func.count().desc(), channel_key)
    )
    async with db.get_async_session() as session:
        result = await session.execute(stmt)
        return [TranscribedChannel(*row) for row in result.all()]


async def get_recent_transcribed_media(
    user_id: int, channel: str, limit: int
) -> list[MediaDetails]:
    """The channel's most recently uploaded transcribed media the user can see."""
    conditions = _transcribed_media_conditions(user_id)
    conditions.append(func.coalesce(MediaDetails.channel, UNKNOWN_CHANNEL) == channel)
    stmt = (
        select(MediaDetails)
        .where(and_(*conditions))
        .order_by(MediaDetails.release_timestamp.desc().nulls_last(), MediaDetails.id.desc())
        .limit(limit)
    )
    async with db.get_async_session() as session:
        result = await session.execute(stmt)
        return list(result.scalars().all())


def _unpack_joined_results(result, has_playback_join: bool) -> tuple[list, dict[int, dict]]:
    """Unpack query results, handling the different shapes from add_columns JOIN vs plain query.

    Returns (records, playback_from_join) where playback_from_join maps
    media_id -> {playback_position, last_accessed, access_count}.
    """
    if has_playback_join:
        rows = result.unique().all()
        records = [row[0] for row in rows]
        playback_from_join = {
            row[0].id: {
                'playback_position': row[1],
                'last_accessed': row[2],
                'access_count': row[3],
            }
            for row in rows
        }
    else:
        records = result.unique().scalars().all()
        playback_from_join = {}

    return records, playback_from_join


def _serialize_media_record(
    record: MediaDetails,
    *,
    playback_data: dict,
    transcript_count: int | None = None,
    rating: int | None = None,
    tags: list[dict] | None = None,
) -> dict:
    """Serialize one MediaDetails record with merged playback state and task progress."""
    record_dict = record.model_dump(mode='json')

    # Merge per-user playback state from the JOIN
    record_dict['playback_position'] = playback_data.get('playback_position')
    record_dict['last_accessed'] = (
        playback_data['last_accessed'].isoformat() if playback_data.get('last_accessed') else None
    )
    record_dict['access_count'] = playback_data.get('access_count') or 0

    # Add joined task record data for real-time progress
    if record.transcript_task_record:
        record_dict['transcript_task_progress'] = record.transcript_task_record.percent_complete
        record_dict['transcript_task_status'] = record.transcript_task_record.status.value
    if record.download_task_record:
        record_dict['download_task_progress'] = record.download_task_record.percent_complete
        record_dict['download_task_status'] = record.download_task_record.status.value

    # Add transcript block count (for deleted records)
    if transcript_count is not None:
        record_dict['transcript_block_count'] = transcript_count

    # Add per-user rating and tags
    record_dict['rating'] = rating
    record_dict['tags'] = tags or []

    return record_dict


async def _fetch_ratings_and_tags(
    session, record_ids: list[int], rating_user_id: int | None
) -> tuple[dict[int, int], dict[int, list[dict]]]:
    """Batch-load one user's ratings and tags for a page of media.

    Returns ({media_id: rating}, {media_id: [{id, name}]}). Two queries total,
    regardless of page size.
    """
    ratings_map: dict[int, int] = {}
    tags_map: dict[int, list[dict]] = {}

    if rating_user_id is None or not record_ids:
        return ratings_map, tags_map

    rating_stmt = sa_select(MediaRating.media_details_id, MediaRating.rating).where(
        and_(
            MediaRating.user_id == rating_user_id,
            MediaRating.media_details_id.in_(record_ids),
        )
    )
    rating_result = await session.execute(rating_stmt)
    ratings_map = {row.media_details_id: row.rating for row in rating_result}

    tags_stmt = (
        sa_select(MediaTag.media_details_id, Tag.id, Tag.name)
        .join(Tag, MediaTag.tag_id == Tag.id)
        .where(
            and_(
                MediaTag.user_id == rating_user_id,
                MediaTag.media_details_id.in_(record_ids),
            )
        )
        .order_by(Tag.name)
    )
    tags_result = await session.execute(tags_stmt)
    for media_id, tag_id, tag_name in tags_result:
        tags_map.setdefault(media_id, []).append({'id': tag_id, 'name': tag_name})

    return ratings_map, tags_map


# --- Async functions for FastAPI ---


async def get_media_stats(
    search: str | None = None,
    status: str | None = None,
    user_id: int | None = None,
    media_types: Sequence[MediaType] | None = None,
    has_transcript: bool = False,
) -> dict:
    """Get media library statistics, optionally filtered by search and status.

    Args:
        search: Optional search string to filter by channel/title (ILIKE)
        status: Optional status filter (defaults to COMPLETE, except in has_transcript mode)
        user_id: Optional user ID to filter by media_access. None = no filter (admin view).
        media_types / has_transcript: as for _build_media_conditions. has_transcript is the
            Transcripts view, which spans every status like the transcript search does.

    Returns:
        dict with total_downloads, total_transcript_blocks, downloads_with_transcripts
    """
    async with db.get_async_session() as session:
        conditions = _build_media_conditions(
            user_id=user_id,
            status=None if has_transcript else status or TaskStatus.COMPLETE,
            search=search,
            media_types=media_types,
            has_transcript=has_transcript,
            include_owned=has_transcript,
        )
        where_clause = and_(*conditions)

        # Count matching downloads
        downloads_stmt = select(func.count()).select_from(MediaDetails).where(where_clause)
        downloads_result = await session.execute(downloads_stmt)
        total_downloads = downloads_result.scalar() or 0

        # Subquery for matching media IDs (used to scope transcript queries)
        matching_ids = select(MediaDetails.id).where(where_clause).subquery()

        # Count transcript blocks for matching media
        blocks_stmt = (
            select(func.count())
            .select_from(TranscriptBlock)
            .where(TranscriptBlock.media_details_id.in_(select(matching_ids.c.id)))
        )
        blocks_result = await session.execute(blocks_stmt)
        total_transcript_blocks = blocks_result.scalar() or 0

        # Count matching downloads with at least one transcript block
        with_transcripts_stmt = select(
            func.count(func.distinct(TranscriptBlock.media_details_id))
        ).where(TranscriptBlock.media_details_id.in_(select(matching_ids.c.id)))
        with_transcripts_result = await session.execute(with_transcripts_stmt)
        downloads_with_transcripts = with_transcripts_result.scalar() or 0

        return {
            'total_downloads': total_downloads,
            'total_transcript_blocks': total_transcript_blocks,
            'downloads_with_transcripts': downloads_with_transcripts,
        }


# --- Media grouping (folder view) ---


def _group_agg_columns(range_field) -> list:
    """Standard per-group aggregate columns. range_field drives min/max date."""
    return [
        func.count().label('count'),
        func.sum(MediaDetails.duration).label('total_duration'),
        func.sum(MediaDetails.file_size_bytes).label('total_size_bytes'),
        func.min(range_field).label('min_date'),
        func.max(range_field).label('max_date'),
        func.sum(case((MediaDetails.media_type == MediaType.VIDEO, 1), else_=0)).label(
            'video_count'
        ),
        func.sum(case((MediaDetails.media_type == MediaType.AUDIO, 1), else_=0)).label(
            'audio_count'
        ),
        func.sum(case((MediaDetails.media_type == MediaType.TRANSCRIPT, 1), else_=0)).label(
            'transcript_count'
        ),
    ]


def _build_group(key: str, label: str, row, sample_ids: list[int]) -> dict:
    return {
        'key': key,
        'label': label,
        'count': int(row.count or 0),
        'total_duration': float(row.total_duration) if row.total_duration is not None else 0.0,
        'total_size_bytes': int(row.total_size_bytes) if row.total_size_bytes is not None else 0,
        'min_date': row.min_date.isoformat() if row.min_date else None,
        'max_date': row.max_date.isoformat() if row.max_date else None,
        'video_count': int(row.video_count or 0),
        'audio_count': int(row.audio_count or 0),
        'transcript_count': int(row.transcript_count or 0),
        'sample_media_ids': list(sample_ids),
    }


def _paginate(stmt, page: int, page_size: int | None):
    if page_size:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    return stmt


async def _count_distinct_groups(session, key_expr, conditions) -> int:
    sub = sa_select(key_expr.label('gkey')).where(and_(*conditions)).group_by(key_expr).subquery()
    return (await session.execute(sa_select(func.count()).select_from(sub))).scalar() or 0


async def _fetch_samples(
    session, key_expr, conditions, page_keys, *, joins=None, order_by=None
) -> dict:
    """Return {raw_group_key: [up to 4 sample media ids]} for the given keys.

    Defaults to the most-recent media per key, those with a thumbnail first so a folder
    mixing transcript-only items (which never have one) still gets a real collage.
    `order_by` lets a caller pick a different notion of "first four" — playlists pass
    PlaylistMedia.position so their collage shows the opening tracks rather than the
    newest ones.
    """
    if not page_keys:
        return {}
    if order_by is None:
        order_by = (
            MediaDetails.thumbnail_path.is_(None),
            MediaDetails.created_at.desc(),
            MediaDetails.id.desc(),
        )
    rn = func.row_number().over(partition_by=key_expr, order_by=order_by).label('rn')
    inner = sa_select(MediaDetails.id.label('mid'), key_expr.label('gkey'), rn).select_from(
        MediaDetails
    )
    for join in joins or []:
        inner = inner.join(*join)
    inner = inner.where(and_(*conditions, key_expr.in_(page_keys))).subquery()
    rows = (
        await session.execute(
            sa_select(inner.c.gkey, inner.c.mid)
            .where(inner.c.rn <= 4)
            .order_by(inner.c.gkey, inner.c.rn)
        )
    ).all()
    out: dict = {}
    for gkey, mid in rows:
        out.setdefault(gkey, []).append(mid)
    return out


async def _media_groups_by_channel(session, base_conditions, page, page_size) -> dict:
    key_expr = func.coalesce(MediaDetails.channel, UNKNOWN_CHANNEL)
    cols = [key_expr.label('gkey'), *_group_agg_columns(MediaDetails.release_timestamp)]
    total = await _count_distinct_groups(session, key_expr, base_conditions)
    stmt = _paginate(
        sa_select(*cols)
        .where(and_(*base_conditions))
        .group_by(key_expr)
        .order_by(func.count().desc(), key_expr.asc()),
        page,
        page_size,
    )
    rows = (await session.execute(stmt)).all()
    samples = await _fetch_samples(session, key_expr, base_conditions, [r.gkey for r in rows])
    groups = [_build_group(r.gkey, r.gkey, r, samples.get(r.gkey, [])) for r in rows]
    return {'page_count': page_count(total, page_size), 'groups': groups}


async def _media_groups_by_date(
    session, base_conditions, group_by, level, parent, page, page_size
) -> dict:
    field = _GROUP_DATE_FIELDS[group_by]
    level = level or 'year'
    conditions = [*list(base_conditions), field.isnot(None)]
    if level == 'month' and parent:
        conditions.append(func.extract('year', field) == int(parent))
    key_expr = func.date_trunc(level, field)
    cols = [key_expr.label('gkey'), *_group_agg_columns(field)]
    total = await _count_distinct_groups(session, key_expr, conditions)
    # Years newest-first; months chronological (Jan -> Dec) within the selected year.
    order = key_expr.asc() if level == 'month' else key_expr.desc()
    stmt = _paginate(
        sa_select(*cols).where(and_(*conditions)).group_by(key_expr).order_by(order),
        page,
        page_size,
    )
    rows = (await session.execute(stmt)).all()
    samples = await _fetch_samples(session, key_expr, conditions, [r.gkey for r in rows])
    groups = []
    for r in rows:
        bucket = r.gkey
        if level == 'year':
            key = label = str(bucket.year)
        else:
            key = f'{bucket.year}-{bucket.month:02d}'
            label = calendar.month_name[bucket.month]
        groups.append(_build_group(key, label, r, samples.get(bucket, [])))
    return {'page_count': page_count(total, page_size), 'groups': groups}


async def _media_groups_by_tag(session, base_conditions, rating_user_id, page, page_size) -> dict:
    if rating_user_id is None:
        return {'page_count': 1, 'groups': []}

    tag_join = (
        MediaTag,
        and_(MediaTag.media_details_id == MediaDetails.id, MediaTag.user_id == rating_user_id),
    )
    tag_name_join = (Tag, Tag.id == MediaTag.tag_id)
    cols = [
        Tag.id.label('gkey'),
        Tag.name.label('tag_name'),
        *_group_agg_columns(MediaDetails.release_timestamp),
    ]
    total_sub = (
        sa_select(Tag.id)
        .select_from(MediaDetails)
        .join(*tag_join)
        .join(*tag_name_join)
        .where(and_(*base_conditions))
        .group_by(Tag.id)
        .subquery()
    )
    total = (await session.execute(sa_select(func.count()).select_from(total_sub))).scalar() or 0
    stmt = _paginate(
        sa_select(*cols)
        .select_from(MediaDetails)
        .join(*tag_join)
        .join(*tag_name_join)
        .where(and_(*base_conditions))
        .group_by(Tag.id, Tag.name)
        .order_by(func.count().desc(), Tag.name.asc()),
        page,
        page_size,
    )
    rows = (await session.execute(stmt)).all()
    samples = await _fetch_samples(
        session, Tag.id, base_conditions, [r.gkey for r in rows], joins=[tag_join, tag_name_join]
    )
    groups = [_build_group(str(r.gkey), r.tag_name, r, samples.get(r.gkey, [])) for r in rows]

    # Untagged bucket (media with no tags for this user) — only on the first page.
    if page == 1:
        untagged_conditions = [
            *list(base_conditions),
            ~MediaDetails.id.in_(
                sa_select(MediaTag.media_details_id).where(MediaTag.user_id == rating_user_id)
            ),
        ]
        u_row = (
            await session.execute(
                sa_select(*_group_agg_columns(MediaDetails.release_timestamp)).where(
                    and_(*untagged_conditions)
                )
            )
        ).one()
        if u_row.count:
            u_samples = (
                (
                    await session.execute(
                        sa_select(MediaDetails.id)
                        .where(and_(*untagged_conditions))
                        .order_by(MediaDetails.created_at.desc(), MediaDetails.id.desc())
                        .limit(4)
                    )
                )
                .scalars()
                .all()
            )
            groups.append(_build_group(UNTAGGED_KEY, UNTAGGED_LABEL, u_row, list(u_samples)))

    return {'page_count': page_count(total, page_size), 'groups': groups}


async def get_media_groups(
    *,
    group_by: str,
    level: str | None = None,
    parent: str | None = None,
    status: str | None = TaskStatus.COMPLETE.value,
    search: str | None = None,
    tag_ids: list[int] | None = None,
    min_rating: int | None = None,
    user_id: int | None = None,
    rating_user_id: int | None = None,
    page: int = 1,
    page_size: int | None = None,
    media_types: Sequence[MediaType] | None = None,
    has_transcript: bool = False,
) -> dict:
    """Aggregate media into folder groups with per-group stats.

    group_by: 'channel' | 'tag' | 'downloaded' | 'released'. Date dimensions use
    level ('year' | 'month'); 'month' requires parent=<year>. Filters (status, search,
    tag_ids, min_rating, user access) are applied via _build_media_conditions, so grouping
    respects the active library filters.

    media_types / has_transcript are as for _build_media_conditions, so the Transcripts
    view's folders count exactly what its list and the transcript search will show.

    Returns {'page_count': int, 'groups': [ {key, label, count, total_duration,
    total_size_bytes, min_date, max_date, video_count, audio_count, transcript_count,
    sample_media_ids} ]}.
    """
    base_conditions = _build_media_conditions(
        user_id=user_id,
        status=None if has_transcript else status,
        search=search,
        tag_ids=tag_ids,
        min_rating=min_rating,
        rating_user_id=rating_user_id,
        media_types=media_types,
        has_transcript=has_transcript,
        include_owned=has_transcript,
    )
    async with db.get_async_session() as session:
        if group_by == 'tag':
            return await _media_groups_by_tag(
                session, base_conditions, rating_user_id, page, page_size
            )
        if group_by in _GROUP_DATE_FIELDS:
            return await _media_groups_by_date(
                session, base_conditions, group_by, level, parent, page, page_size
            )
        if group_by == 'channel':
            return await _media_groups_by_channel(session, base_conditions, page, page_size)
        msg = f'Invalid group_by: {group_by}'
        raise ValueError(msg)


async def get_media_details_by_url_and_media_type(
    url: str, media_type_value: str, task_status_exclude: TaskStatus = TaskStatus.DELETED
) -> MediaDetails | None:
    async with db.get_async_session() as session:
        stmt = select(MediaDetails).where(
            and_(
                MediaDetails.url == url,
                MediaDetails.media_type == media_type_value,
                MediaDetails.status != task_status_exclude,
            )
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_deleted_media_by_url_type_owner(
    url: str, media_type_value: str, owner_id: int
) -> MediaDetails | None:
    """Get the owner's soft-deleted (DELETED) MediaDetails for a URL/media type.

    The default get_media_details_by_url_and_media_type excludes DELETED rows, so this
    is the lookup used to detect an owner re-requesting a video they deliberately deleted.
    """
    async with db.get_async_session() as session:
        stmt = select(MediaDetails).where(
            and_(
                MediaDetails.url == url,
                MediaDetails.media_type == media_type_value,
                MediaDetails.status == TaskStatus.DELETED,
                MediaDetails.owner_id == owner_id,
            )
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


def has_playable_file(media: MediaDetails) -> bool:
    """Whether the app holds a finished file of this item it can stream or cut.

    A transcript-only item never does, even mid-chain while its `file_path` holds the
    audio staged for Whisper: that file is not the media and is deleted once the
    transcript lands.
    """
    return (
        media.status == TaskStatus.COMPLETE
        and bool(media.file_path)
        and media.media_type != MediaType.TRANSCRIPT
    )


async def get_media_details_by_id(id: int) -> MediaDetails | None:
    async with db.get_async_session() as session:
        stmt = select(MediaDetails).where(MediaDetails.id == id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_media_details_by_ids(ids: Sequence[int]) -> list[MediaDetails]:
    if not ids:
        return []
    async with db.get_async_session() as session:
        stmt = select(MediaDetails).where(MediaDetails.id.in_(ids))
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_media_details_by_transcript_task_record_id(
    task_record_id: int,
) -> MediaDetails | None:
    async with db.get_async_session() as session:
        stmt = select(MediaDetails).where(MediaDetails.transcript_task_record_id == task_record_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_media_details_by_download_task_record_id(
    task_record_id: int,
) -> MediaDetails | None:
    async with db.get_async_session() as session:
        stmt = select(MediaDetails).where(MediaDetails.download_task_record_id == task_record_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def soft_delete_media_details_by_id(id: int, keep_thumbnail: bool = False) -> int:
    """
    Soft delete a MediaDetails record:
    - Deletes the media file from disk, and its thumbnail unless `keep_thumbnail`
    - Marks the record as DELETED (preserves record for skip logic)
    - Clears file_path and transcript fields

    `keep_thumbnail` is for a delete that keeps the transcript: the Transcripts view still
    lists the item, so its thumbnail moves out of the media folder to its kept path.

    Returns:
        Number of records modified (0 or 1)
    """
    async with db.get_async_session() as session:
        stmt = select(MediaDetails).where(MediaDetails.id == id)
        result = await session.execute(stmt)
        md = result.scalar_one_or_none()

        if not md:
            return 0

        thumbnail_path = None
        if keep_thumbnail and md.thumbnail_path:
            # Before delete_file below, whose sidecar cleanup would take it.
            thumbnail_path = move_thumbnail(
                md.thumbnail_path, kept_thumbnail_path(md.url, md.media_type)
            )
        else:
            delete_thumbnail(md.thumbnail_path)

        if md.file_path:
            delete_file(md.file_path, cleanup_sidecars=True)
            logger.info(f'Deleted file: {md.file_path}')

        md.status = TaskStatus.DELETED
        md.file_path = None
        md.file_size_bytes = None
        md.thumbnail_path = thumbnail_path
        md.transcript_task_record_id = None

        await session.commit()
        logger.info(f'Soft deleted MediaDetails with id: {id}')
        return 1


async def transfer_ownership(media_details_id: int, new_owner_id: int) -> bool:
    """Transfer ownership of a MediaDetails record to a new user.

    Updates only owner_id — status, file_path, transcripts all stay unchanged.
    Returns True if the record was found and updated, False otherwise.
    """
    async with db.get_async_session() as session:
        stmt = select(MediaDetails).where(MediaDetails.id == media_details_id)
        result = await session.execute(stmt)
        md = result.scalar_one_or_none()

        if not md:
            return False

        md.owner_id = new_owner_id
        await session.commit()
        return True


async def get_all_media_details(
    search: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
    sort_by: str | None = None,
    sort_direction: str = 'desc',
    user_id: int | None = None,
    playback_user_id: int | None = None,
    tag_ids: list[int] | None = None,
    min_rating: int | None = None,
    rating_user_id: int | None = None,
    channel: str | None = None,
    untagged: bool = False,
    date_field: str | None = None,
    date_year: int | None = None,
    date_month: int | None = None,
    media_types: Sequence[MediaType] | None = None,
    has_transcript: bool = False,
) -> dict:
    """Get all MediaDetails with optional filtering and pagination.

    Args:
        user_id: Optional user ID to filter by media_access. None = no filter (admin view).
        tag_ids: Optional list of tag IDs — filter to media with ANY of these tags.
        min_rating: Optional minimum star rating (1-5).
        rating_user_id: User whose tags/ratings to use for filtering and serialization.
        channel / untagged / date_field+date_year(+date_month): group-folder drill-down
            filters (see get_media_groups).
        media_types / has_transcript: as for _build_media_conditions. has_transcript is the
            Transcripts view: every status, with the same widened access as the search.
    """
    logger.info(
        f'get_all_media_details called with sort_by={sort_by}, sort_direction={sort_direction}'
    )
    async with db.get_async_session() as session:
        # Build query with eager loading of task records
        conditions = _build_media_conditions(
            user_id=user_id,
            status=None if has_transcript else status,
            search=search,
            tag_ids=tag_ids,
            min_rating=min_rating,
            rating_user_id=rating_user_id,
            channel=channel,
            untagged=untagged,
            date_field=date_field,
            date_year=date_year,
            date_month=date_month,
            media_types=media_types,
            has_transcript=has_transcript,
            include_owned=has_transcript,
        )
        stmt = select(MediaDetails).options(
            joinedload(MediaDetails.transcript_task_record),
            joinedload(MediaDetails.download_task_record),
        )
        if conditions:
            stmt = stmt.where(and_(*conditions))

        # Count total records (need separate query without joinedload)
        count_stmt = select(func.count()).select_from(
            select(MediaDetails.id).where(and_(*conditions) if conditions else True).subquery()
        )
        count_result = await session.execute(count_stmt)
        count_records = count_result.scalar()

        # LEFT JOIN PlaybackState when we need playback data for this user
        ps_alias = None
        if playback_user_id is not None:
            ps_alias = (
                select(PlaybackState).where(PlaybackState.user_id == playback_user_id).subquery()
            )
            stmt = stmt.outerjoin(ps_alias, MediaDetails.id == ps_alias.c.media_details_id)
            stmt = stmt.add_columns(
                ps_alias.c.playback_position,
                ps_alias.c.last_accessed,
                ps_alias.c.access_count,
            )

        # LEFT JOIN MediaRating for rating sort
        rating_alias = None
        if sort_by == 'rating' and rating_user_id is not None:
            rating_alias = (
                select(MediaRating).where(MediaRating.user_id == rating_user_id).subquery()
            )
            stmt = stmt.outerjoin(rating_alias, MediaDetails.id == rating_alias.c.media_details_id)

        # Apply ordering — playback fields live in PlaybackState, everything else on MediaDetails
        playback_sort_fields = {'last_accessed', 'access_count', 'playback_position'}
        if sort_by == 'rating' and rating_alias is not None:
            sort_column = rating_alias.c.rating
            logger.debug(f'Sorting by rating {sort_direction}')
            if sort_direction == 'asc':
                stmt = stmt.order_by(asc(sort_column).nullslast(), MediaDetails.id.asc())
            else:
                stmt = stmt.order_by(desc(sort_column).nullslast(), MediaDetails.id.desc())
        elif sort_by and sort_by in playback_sort_fields and ps_alias is not None:
            sort_column = ps_alias.c[sort_by]
            logger.debug(f'Sorting by PlaybackState.{sort_by} {sort_direction}')
            if sort_direction == 'asc':
                stmt = stmt.order_by(asc(sort_column).nullsfirst(), MediaDetails.id.asc())
            else:
                stmt = stmt.order_by(desc(sort_column).nullslast(), MediaDetails.id.desc())
        elif sort_by and hasattr(MediaDetails, sort_by):
            sort_column = getattr(MediaDetails, sort_by)
            logger.debug(f'Sorting by {sort_by} {sort_direction}')
            if sort_direction == 'asc':
                stmt = stmt.order_by(asc(sort_column).nullsfirst(), MediaDetails.id.asc())
            else:
                stmt = stmt.order_by(desc(sort_column).nullslast(), MediaDetails.id.desc())
        else:
            logger.debug(
                f'Using default sort (sort_by={sort_by}, hasattr={hasattr(MediaDetails, sort_by) if sort_by else "N/A"})'
            )
            stmt = stmt.order_by(MediaDetails.id.desc())

        # Apply pagination
        if page_size is not None:
            stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        result = await session.execute(stmt)
        records, playback_from_join = _unpack_joined_results(result, ps_alias is not None)

        record_ids = [r.id for r in records]

        # For DELETED status and the Transcripts view, fetch transcript block counts in
        # a single query
        with_transcript_counts = has_transcript or status == 'DELETED'
        transcript_counts = {}
        if with_transcript_counts and records:
            count_stmt = (
                sa_select(
                    TranscriptBlock.media_details_id,
                    func.count(TranscriptBlock.id).label('count'),
                )
                .where(TranscriptBlock.media_details_id.in_(record_ids))
                .group_by(TranscriptBlock.media_details_id)
            )
            count_result = await session.execute(count_stmt)
            transcript_counts = {row.media_details_id: row.count for row in count_result}

        ratings_map, tags_map = await _fetch_ratings_and_tags(session, record_ids, rating_user_id)

        serialized_records = [
            _serialize_media_record(
                record,
                playback_data=playback_from_join.get(record.id, {}),
                transcript_count=transcript_counts.get(record.id, 0)
                if with_transcript_counts
                else None,
                rating=ratings_map.get(record.id),
                tags=tags_map.get(record.id, []),
            )
            for record in records
        ]

        return {
            'count_records': count_records,
            'page_count': page_count(count_records, page_size),
            'records': serialized_records,
        }


async def hard_delete_media_details_by_id(id: int) -> int:
    """Hard delete a MediaDetails record (true row deletion).

    Only works on records with status=DELETED (safety check).
    Cascade will automatically delete transcript blocks and embeddings.

    Returns:
        Number of records deleted (0 or 1)
    """
    async with db.get_async_session() as session:
        stmt = select(MediaDetails).where(
            and_(MediaDetails.id == id, MediaDetails.status == TaskStatus.DELETED)
        )
        result = await session.execute(stmt)
        md = result.scalar_one_or_none()

        if not md:
            return 0

        delete_thumbnail(md.thumbnail_path)
        await session.delete(md)
        await session.commit()
        logger.info(f'Hard deleted MediaDetails with id: {id}')
        return 1


async def update_one(id: int, updated_params: dict) -> MediaDetails | None:
    """Update a MediaDetails record by ID and return the updated record."""
    async with db.get_async_session() as session:
        stmt = select(MediaDetails).where(MediaDetails.id == id)
        result = await session.execute(stmt)
        md = result.scalar_one_or_none()

        if not md:
            return None

        for key, value in updated_params.items():
            if hasattr(md, key):
                setattr(md, key, value)

        await session.commit()
        await session.refresh(md)
        return md


def _upsert_lookup_stmt(url: str, media_type):
    """The (url, media_type) unique key behind uq_media_details_url_type.

    Shared by the async and sync upserts so the key definition cannot drift; both
    re-run it after an IntegrityError to find the row that won the insert race.
    """
    return select(MediaDetails).where(
        and_(MediaDetails.url == url, MediaDetails.media_type == media_type)
    )


async def upsert_media_details(media_details: MediaDetails) -> MediaDetails:
    """Insert if not exists, otherwise update existing. Uses url + media_type as unique key.

    Safe under concurrent inserts: if a parallel chain wins the insert race on
    uq_media_details_url_type, falls back to updating the winner's row instead of
    raising IntegrityError.
    """
    stmt = _upsert_lookup_stmt(media_details.url, media_details.media_type)
    async with db.get_async_session() as session:
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is None:
            session.add(media_details)
            try:
                await session.flush()
            except IntegrityError:
                # Concurrent insert won the race (our flush blocked on the unique
                # index until it committed) — update the winner's row instead.
                await session.rollback()
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()
                if existing is None:
                    raise
            else:
                await session.commit()
                await session.refresh(media_details)
                return media_details

        _copy_upsert_fields(media_details, existing)
        await session.commit()
        await session.refresh(existing)
        return existing


# --- Sync functions (job bodies run in lane threads / the ML child) ---


def sync_get_media_details_by_url_and_media_type(
    url: str, media_type_value: str
) -> MediaDetails | None:
    """Sync version: Get MediaDetails by URL and media type."""
    with db.sync_session() as session:
        stmt = select(MediaDetails).where(
            and_(MediaDetails.url == url, MediaDetails.media_type == media_type_value)
        )
        result = session.execute(stmt)
        return result.scalar_one_or_none()


# Statuses a cancel may overwrite. COMPLETE/SKIPPED/DELETED/NOT_READY must survive —
# notably so cancelling a transcript task cannot disown a finished download.
_CANCELLABLE_MEDIA_STATUSES = (
    TaskStatus.NONE,
    TaskStatus.QUEUED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.POSTPROCESSING,
    TaskStatus.RETRY,
)


def _cancel_media_stmt(url: str, media_type_value: str | None):
    return (
        sa_update(MediaDetails)
        .where(
            and_(
                MediaDetails.url == url,
                MediaDetails.media_type == media_type_value,
                MediaDetails.status.in_(_CANCELLABLE_MEDIA_STATUSES),
            )
        )
        .values(status=TaskStatus.CANCELLED)
    )


async def mark_download_cancelled(url: str, media_type_value: str | None) -> int:
    """Give a cancelled download's media row a terminal status.

    Cancel is the one lifecycle path that never wrote one: before_start/on_success/
    on_failure all set MediaDetails.status, so a cancelled row keeps whatever populate
    left (NONE) or IN_PROGRESS. NONE is not in _FILTER_SKIP_STATUSES, so every later
    subscription tick re-includes the URL and spawns a populate job that can never
    produce a download — the CANCELLED TaskRecord blocks task creation — forever.

    Returns:
        Number of rows marked (0 if the row is already in a terminal state)
    """
    async with db.get_async_session() as session:
        result = await session.execute(_cancel_media_stmt(url, media_type_value))
        return result.rowcount


def sync_mark_download_cancelled(url: str, media_type_value: str | None) -> int:
    """Sync version of mark_download_cancelled (runs from lane-thread hooks)."""
    with db.sync_session() as session:
        return session.execute(_cancel_media_stmt(url, media_type_value)).rowcount


def sync_get_in_flight_transcript_only_urls() -> list[str]:
    """URLs of transcript-only items whose staged files may still be needed."""
    stmt = sa_select(MediaDetails.url).where(
        and_(
            MediaDetails.media_type == MediaType.TRANSCRIPT,
            MediaDetails.status.in_(_CANCELLABLE_MEDIA_STATUSES),
        )
    )
    with db.sync_session() as session:
        return list(session.execute(stmt).scalars())


def sync_get_thumbnail_paths_under(directory: str) -> set[str]:
    """Every thumbnail_path stored under `directory`, for the orphan sweep."""
    stmt = sa_select(MediaDetails.thumbnail_path).where(
        MediaDetails.thumbnail_path.startswith(directory.rstrip('/') + '/', autoescape=True)
    )
    with db.sync_session() as session:
        return set(session.execute(stmt).scalars())


def _finish_transcript_only_stmts(
    url: str, status: TaskStatus, thumbnail_path: str | None = None
) -> tuple:
    """Record how a transcript-only item's transcript ended, its staged files already gone.

    file_path always clears, since whatever it pointed at was staged and has been deleted.
    `thumbnail_path` is the kept thumbnail of a COMPLETE one; it only ever sets the column,
    so a late hook without one can't clear a thumbnail an earlier run kept.
    The status moves only off an in-flight one, the same guard a cancel uses, so a late
    hook can't knock a COMPLETE row back.
    """
    row = and_(MediaDetails.url == url, MediaDetails.media_type == MediaType.TRANSCRIPT)
    values: dict = {'status': status}
    if status == TaskStatus.COMPLETE:
        values['downloaded_at'] = utc_now()
    cleared: dict = {'file_path': None, 'file_size_bytes': None}
    if thumbnail_path:
        cleared['thumbnail_path'] = thumbnail_path
    return (
        sa_update(MediaDetails).where(row).values(**cleared),
        sa_update(MediaDetails)
        .where(and_(row, MediaDetails.status.in_(_CANCELLABLE_MEDIA_STATUSES)))
        .values(**values),
    )


async def finish_transcript_only(url: str, status: TaskStatus) -> None:
    """See _finish_transcript_only_stmts. For the cancel endpoints."""
    async with db.get_async_session() as session:
        for stmt in _finish_transcript_only_stmts(url, status):
            await session.execute(stmt)
        await session.commit()


def sync_finish_transcript_only(
    url: str, status: TaskStatus, thumbnail_path: str | None = None
) -> None:
    """See _finish_transcript_only_stmts. For TranscriptHooks."""
    with db.sync_session() as session:
        for stmt in _finish_transcript_only_stmts(url, status, thumbnail_path):
            session.execute(stmt)


async def has_full_transcript(url: str, user_id: int) -> bool:
    """Whether the user can already search `url` through a full media row's transcript."""
    stmt = sa_select(
        exists().where(
            and_(
                MediaDetails.url == url,
                MediaDetails.media_type != MediaType.TRANSCRIPT,
                _accessible_to(user_id, MediaDetails),
                exists().where(TranscriptBlock.media_details_id == MediaDetails.id),
            )
        )
    )
    async with db.get_async_session() as session:
        return bool((await session.execute(stmt)).scalar())


def sync_get_urls_with_full_transcript(urls: list[str], user_id: int | None) -> set[str]:
    """Which of `urls` the user can already search through a full media row's transcript.

    The subscription filter's batched form of _not_superseded_condition: a transcript-only
    job for one of these is dropped. user_id None (an unowned subscription) counts every
    full row as visible.
    """
    found: set[str] = set()
    unique_urls = list(dict.fromkeys(urls))

    with db.sync_session() as session:
        for start in range(0, len(unique_urls), _URL_BATCH_SIZE):
            chunk = unique_urls[start : start + _URL_BATCH_SIZE]
            conditions = [
                MediaDetails.url.in_(chunk),
                MediaDetails.media_type != MediaType.TRANSCRIPT,
                exists().where(TranscriptBlock.media_details_id == MediaDetails.id),
            ]
            if user_id is not None:
                conditions.append(_accessible_to(user_id, MediaDetails))
            stmt = sa_select(MediaDetails.url).where(and_(*conditions)).distinct()
            found.update(session.execute(stmt).scalars())

    return found


def sync_get_media_details_by_urls(
    urls: list[str], media_type_value: str | None
) -> dict[str, MediaDetails]:
    """Sync version: map url -> MediaDetails for one media type.

    Chunked so a whole-channel enumeration (thousands of URLs) stays under Postgres'
    bind-parameter ceiling. Backed by uq_media_details_url_type.

    Args:
        urls: Video URLs, already normalized to the form MediaDetails is keyed on
        media_type_value: The media type value ('AUDIO'/'VIDEO') or None

    Returns:
        Dict of url -> MediaDetails for the URLs that exist
    """
    found: dict[str, MediaDetails] = {}
    unique_urls = list(dict.fromkeys(urls))

    with db.sync_session() as session:
        for start in range(0, len(unique_urls), _URL_BATCH_SIZE):
            chunk = unique_urls[start : start + _URL_BATCH_SIZE]
            stmt = select(MediaDetails).where(
                and_(MediaDetails.url.in_(chunk), MediaDetails.media_type == media_type_value)
            )
            for md in session.execute(stmt).scalars():
                found[md.url] = md

    return found


def sync_upsert_deferred_media(
    media_details: MediaDetails, next_check_at: datetime
) -> MediaDetails:
    """Sync version: upsert a NOT_READY row for a video that can't be downloaded yet.

    status and next_check_at are written explicitly because neither travels through the
    shared upsert: next_check_at is absent from _UPSERT_FIELDS (the download path must
    never disturb the re-check clock).

    Args:
        media_details: The row to upsert; status is forced to NOT_READY
        next_check_at: Earliest time a subscription tick should re-evaluate this URL

    Returns:
        The persisted MediaDetails
    """
    media_details.status = TaskStatus.NOT_READY
    persisted = sync_upsert_media_details(media_details)
    sync_update_one(persisted.id, {'next_check_at': next_check_at})
    persisted.next_check_at = next_check_at
    return persisted


def sync_clear_deferral(url: str, media_type_value: str | None) -> int:
    """Sync version: drop the deferral once a NOT_READY video resolves.

    Resets status to NONE and next_check_at to NULL so the normal persist path can take
    over. Needed because _copy_upsert_fields deliberately refuses to write status when
    it is NONE, so a plain upsert cannot clear NOT_READY on its own.

    Returns:
        Number of rows cleared (0 or 1)
    """
    with db.sync_session() as session:
        stmt = select(MediaDetails).where(
            and_(
                MediaDetails.url == url,
                MediaDetails.media_type == media_type_value,
                MediaDetails.status == TaskStatus.NOT_READY,
            )
        )
        row = session.execute(stmt).scalar_one_or_none()
        if row is None:
            return 0
        row.status = TaskStatus.NONE
        row.next_check_at = None
        return 1


def sync_get_media_details_by_id(id: int) -> MediaDetails | None:
    """Sync version: Get MediaDetails by ID."""
    with db.sync_session() as session:
        stmt = select(MediaDetails).where(MediaDetails.id == id)
        result = session.execute(stmt)
        return result.scalar_one_or_none()


def sync_update_one(id: int, updated_params: dict) -> int:
    """Sync version: Update a MediaDetails record by ID."""
    with db.sync_session() as session:
        stmt = select(MediaDetails).where(MediaDetails.id == id)
        result = session.execute(stmt)
        md = result.scalar_one_or_none()

        if not md:
            return 0

        for key, value in updated_params.items():
            if hasattr(md, key):
                setattr(md, key, value)

        return 1


def sync_upsert_media_details(media_details: MediaDetails) -> MediaDetails:
    """Sync version: Insert if not exists, otherwise update existing.

    Safe under concurrent inserts: if a parallel chain wins the insert race on
    uq_media_details_url_type, falls back to updating the winner's row instead of
    raising IntegrityError.
    """
    stmt = _upsert_lookup_stmt(media_details.url, media_details.media_type)
    with db.sync_session() as session:
        result = session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is None:
            session.add(media_details)
            try:
                session.flush()
            except IntegrityError:
                # Concurrent insert won the race (our flush blocked on the unique
                # index until it committed) — update the winner's row instead.
                session.rollback()
                result = session.execute(stmt)
                existing = result.scalar_one_or_none()
                if existing is None:
                    raise
            else:
                session.refresh(media_details)
                return media_details

        _copy_upsert_fields(media_details, existing)
        session.flush()
        session.refresh(existing)
        return existing


def sync_delete_by_url_and_media_type(media_details: MediaDetails) -> int:
    """Sync version: Delete MediaDetails by URL and media type."""
    with db.sync_session() as session:
        stmt = delete(MediaDetails).where(
            and_(
                MediaDetails.url == media_details.url,
                MediaDetails.media_type == media_details.media_type,
            )
        )
        result = session.execute(stmt)
        return result.rowcount


def sync_update_by_id(media_id: int, **updates) -> MediaDetails | None:
    """Update a MediaDetails record by ID and return the updated record.

    Consolidates the common pattern of: fetch by ID, apply updates, save.

    Args:
        media_id: The primary key ID of the MediaDetails
        **updates: Keyword arguments for fields to update

    Returns:
        The updated MediaDetails or None if not found
    """
    with db.sync_session() as session:
        stmt = select(MediaDetails).where(MediaDetails.id == media_id)
        result = session.execute(stmt)
        md = result.scalar_one_or_none()

        if not md:
            return None

        for key, value in updates.items():
            if hasattr(md, key):
                setattr(md, key, value)

        session.flush()
        session.refresh(md)
        return md
