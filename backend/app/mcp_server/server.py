"""The MCP server: read-only search over the caller's transcripts, plus a research prompt.

Every tool acts as the principal the HTTP endpoint authenticated, and sees what that
user sees in the app without admin view: their own media and media shared with them.
"""

import difflib
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from math import ceil
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from config import settings
from mcp_server.context import current_principal
from mcp_server.formatting import (
    AppliedFilters,
    AppliedSearch,
    ChannelSummary,
    LibraryOverview,
    MentionRow,
    MentionTimeline,
    SearchResult,
    TagSummary,
    TranscriptSegment,
    TranscriptWindow,
    VideoEntry,
    VideoList,
    build_mention_timeline,
    build_search_result,
    build_video_list,
    iso_date,
    media_app_url,
    merge_adjacent_hits,
    render_overview,
    render_transcript,
)
from mcp_server.links import format_timestamp, source_url
from repositories import media_access as ma_repo
from repositories import media_details as md_repo
from repositories import tags as tags_repo
from repositories import transcript_blocks as tb_repo
from repositories.media_details import UNKNOWN_CHANNEL
from services.embeddings import OnnxEmbedder
from services.transcript import (
    describe_fts_query,
    get_hybrid_search_results,
    get_mention_timeline,
)
from utils import load_embedding_model

SEARCH_DEFAULT_LIMIT = 15
SEARCH_MAX_LIMIT = 40
# Raw hits every page of one search fetches, whatever its offset: merging the same hits
# gives the same passages on every page, and all of them come from one cache entry.
SEARCH_FETCH_DEPTH = 400
MEDIA_IDS_MAX = 100
# Hybrid semantic/keyword balance when keywords are given; the Downloads slider's midpoint.
HYBRID_SEMANTIC_WEIGHT = 0.5
CHANNEL_PAGE = 100
VIDEO_PAGE_DEFAULT = 50
VIDEO_PAGE_MAX = 100
TIMELINE_TERMS_MAX = 10
TIMELINE_TERM_MAX_CHARS = 200
TRANSCRIPT_DEFAULT_CHARS = 8_000
TRANSCRIPT_MIN_CHARS = 500
TRANSCRIPT_MAX_CHARS = 20_000
# ~2.8 h of 20 s blocks: one read never needs more, and max_chars stops far sooner.
TRANSCRIPT_BLOCK_FETCH = 500
CHANNEL_FUZZY_CUTOFF = 0.75
# The operators `_build_search_condition` splits on; a value of only these has no terms.
TITLE_OPERATORS = re.compile(r'&&|\|\|')

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = """\
Search the user's personal video library (YouTube downloads) by what was said in it. \
Use these tools to research what channels said about a topic, over a time range, with \
citations.

1. library_overview: channels, upload-date span and tags. Call it first when the \
question names a channel or a period, to get exact channel names.
2. search_transcripts: timestamped passages, best first, a page at a time. Phrase \
`query` like the passage you hope to find; put exact names or terms in `keywords`, and \
"double quotes" around an exact phrase. `keywords` alone finds every passage containing \
them. Run several searches with different phrasings for broad questions, and follow \
`next_offset` while the passages stay relevant. Narrow by video title with \
`title_contains`, or to a shortlist with `media_ids`.
3. mention_timeline: how often terms came up per upload month or year, counted over \
every transcript. Use it for when and how much, then search a busy period.
4. list_videos: every transcribed video of a channel or period, page by page, to cover \
it exhaustively or to pick media_ids.
5. get_transcript: read around a passage (start a little before its timestamp) to quote \
it accurately, or read a whole video by following `next_start_seconds`.

When answering: cite every claim with the passage's linked id exactly as given, \
e.g. [B1234](link), and give each cited passage's upload date. End with an evidence table: \
Uploaded | Channel | Video | Time (source link / app link) | Quote. Group findings \
chronologically when the question spans a period. Only state what the passages say."""

RESEARCH_PROMPT = """\
Research my video transcript library to answer the question I ask next.

- Start with library_overview if I mention a channel, a tag or a time period.
- If the question is about when or how often, start with mention_timeline.
- Run several search_transcripts calls with different phrasings; use keywords for exact \
names. Narrow with channels, title_contains and released_from / released_to when I give them. \
Follow next_offset to go deeper while the passages are still relevant.
- To cover a channel or period completely, list it with list_videos and read the videos \
with get_transcript.
- Read context with get_transcript before quoting anything ambiguous.

Answer with:
1. A summary, organised chronologically if the question spans time, citing passages by \
their linked [B…] ids exactly as the tools give them, with their upload dates.
2. An evidence table with columns: Uploaded | Channel | Video | Time | Quote. In Time, \
link the timestamp to the source link, and add the app link (▶) when there is one.
3. What the transcripts did not cover, if anything.

Only report what the passages say. Tell me which searches you ran."""


def _tool_result(model: BaseModel, text: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type='text', text=text)],
        structured_content=model.model_dump(mode='json'),
    )


def parse_date_bound(value: str, *, end: bool) -> datetime:
    """`YYYY`, `YYYY-MM` or `YYYY-MM-DD` as the start of that period.

    With `end=True`, the start of the *next* period instead: the exclusive bound that
    makes an inclusive "up to 2025-03" cover all of March.
    """
    try:
        parts = [int(part) for part in value.strip().split('-')]
        if len(parts) == 1:
            start, after = datetime(parts[0], 1, 1), datetime(parts[0] + 1, 1, 1)
        elif len(parts) == 2:
            year, month = parts
            start = datetime(year, month, 1)
            after = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
        elif len(parts) == 3:
            start = datetime(*parts)
            after = start + timedelta(days=1)
        else:
            raise ValueError  # noqa: TRY301 — one message for every malformed shape
    except ValueError:
        msg = f'Invalid date "{value}": use YYYY, YYYY-MM or YYYY-MM-DD.'
        raise ToolError(msg) from None
    return after if end else start


def resolve_channels(requested: list[str], known: list[str]) -> tuple[list[str], list[str]]:
    """Map requested channel names onto real ones: exact (any case), substring, then fuzzy.

    Returns (matched, unmatched). A model passes channel names from memory or from the
    user's phrasing, which rarely match the stored spelling exactly.
    """
    by_lower = {name.lower(): name for name in known}
    matched: list[str] = []
    unmatched: list[str] = []
    for name in requested:
        needle = name.strip().lower()
        if needle in by_lower:
            found = [by_lower[needle]]
        else:
            found = [k for k in known if needle in k.lower()] or [
                by_lower[m]
                for m in difflib.get_close_matches(
                    needle, list(by_lower), n=3, cutoff=CHANNEL_FUZZY_CUTOFF
                )
            ]
        if found:
            matched.extend(f for f in found if f not in matched)
        else:
            unmatched.append(name.strip())
    return matched, unmatched


def _non_blank(values: list[str] | None) -> list[str]:
    return [v for v in (values or []) if v.strip()]


async def _resolve_library_filters(
    user_id: int,
    applied: AppliedFilters,
    *,
    channels: list[str] | None,
    title_contains: str | None,
    tags: list[str] | None,
    min_rating: int | None,
    released_from: str | None,
    released_to: str | None,
) -> dict | None:
    """Validate and resolve the library filters the listing tools share.

    Records what was applied on `applied` and returns the scope keywords the search and
    listing queries take: channels, title_contains, tag_ids, min_rating, released_from
    and released_to (exclusive). Returns None, with a note on `applied`, when a
    requested channel or tag matched nothing: dropping that filter would answer a
    different question.
    """
    title_contains = (title_contains or '').strip() or None
    if title_contains and not any(term.strip() for term in TITLE_OPERATORS.split(title_contains)):
        msg = 'title_contains has no search terms: give text to find in the title.'
        raise ToolError(msg)
    if min_rating is not None and not 1 <= min_rating <= 5:
        msg = 'min_rating must be between 1 and 5.'
        raise ToolError(msg)
    date_from = parse_date_bound(released_from, end=False) if released_from else None
    date_to = parse_date_bound(released_to, end=True) if released_to else None
    if date_from and date_to and date_from >= date_to:
        msg = 'released_from must not be after released_to.'
        raise ToolError(msg)

    applied.title_contains = title_contains
    applied.min_rating = min_rating
    applied.released_from = date_from.date().isoformat() if date_from else None
    applied.released_to = (date_to - timedelta(days=1)).date().isoformat() if date_to else None
    if date_from or date_to:
        applied.notes.append('Videos with no known upload date are excluded by a date range.')

    requested_channels = _non_blank(channels)
    resolved_channels: list[str] | None = None
    if requested_channels:
        known = [c.channel for c in await md_repo.get_transcribed_channels(user_id)]
        resolved_channels, applied.unmatched_channels = resolve_channels(requested_channels, known)
        applied.channels = resolved_channels

    requested_tags = _non_blank(tags)
    tag_ids: list[int] | None = None
    if requested_tags:
        found = await tags_repo.find_tags_by_name(user_id, requested_tags)
        tag_ids = [t.id for t in found]
        found_names = {t.name.lower() for t in found}
        applied.tags = sorted(t.name for t in found)
        applied.unmatched_tags = [
            t.strip() for t in requested_tags if t.strip().lower() not in found_names
        ]

    if (requested_channels and not resolved_channels) or (requested_tags and not tag_ids):
        applied.notes.append('Nothing was searched: a requested filter matched nothing.')
        return None
    return {
        'channels': resolved_channels,
        'title_contains': title_contains,
        'tag_ids': tag_ids,
        'min_rating': min_rating,
        'released_from': date_from,
        'released_to': date_to,
    }


ChannelsFilter = Annotated[
    list[str] | None, Field(description='Only these channels; close spellings are matched.')
]
TitleFilter = Annotated[
    str | None,
    Field(
        description=(
            'Only videos whose title contains this text, ignoring case. Use && for all of '
            'several terms and || for any of them. Matches titles only: channels filters by '
            'channel, keywords searches what was said.'
        )
    ),
]
TagsFilter = Annotated[
    list[str] | None, Field(description="Only media with any of these of the user's tags.")
]
MinRatingFilter = Annotated[
    int | None, Field(description='Only media the user rated at least this, 1-5.')
]
ReleasedFromFilter = Annotated[
    str | None, Field(description='Earliest upload date: YYYY, YYYY-MM or YYYY-MM-DD.')
]
ReleasedToFilter = Annotated[
    str | None, Field(description='Latest upload date, inclusive: YYYY, YYYY-MM or YYYY-MM-DD.')
]


# Swapped by build_mcp_server, so tests can stand in for the ONNX model.
_embedder_factory: Callable[[], OnnxEmbedder] = load_embedding_model


async def library_overview(
    channel_offset: Annotated[
        int,
        Field(
            description=(
                'Channels to skip, largest first: pass next_channel_offset to see the rest '
                f'of a library with more than {CHANNEL_PAGE} channels.'
            )
        ),
    ] = 0,
) -> Annotated[CallToolResult, LibraryOverview]:
    """What the transcript library holds: channels, upload-date span and tags.

    Call it first to pick exact channel names and dates for the other tools.
    """
    principal = current_principal()
    channel_offset = max(0, channel_offset)
    channel_stats = await md_repo.get_transcribed_channels(principal.user_id)
    tags = await tags_repo.get_user_tags(principal.user_id)
    dated = [c for c in channel_stats if c.first_release is not None]
    page_end = channel_offset + CHANNEL_PAGE
    overview = LibraryOverview(
        videos_with_transcripts=sum(c.video_count for c in channel_stats),
        first_upload=iso_date(min((c.first_release for c in dated), default=None)),
        last_upload=iso_date(max((c.last_release for c in dated), default=None)),
        undated_videos=sum(c.undated_count for c in channel_stats),
        channel_count=len(channel_stats),
        channels=[
            ChannelSummary(
                name=c.channel,
                videos=c.video_count,
                first_upload=iso_date(c.first_release),
                last_upload=iso_date(c.last_release),
            )
            for c in channel_stats[channel_offset:page_end]
        ],
        tags=[TagSummary(name=t['name'], videos=t['usage_count']) for t in tags],
        channel_offset=channel_offset,
        next_channel_offset=page_end if page_end < len(channel_stats) else None,
    )
    return _tool_result(overview, render_overview(overview))


async def list_videos(
    channels: ChannelsFilter = None,
    title_contains: TitleFilter = None,
    tags: TagsFilter = None,
    min_rating: MinRatingFilter = None,
    released_from: ReleasedFromFilter = None,
    released_to: ReleasedToFilter = None,
    sort: Annotated[
        Literal['newest', 'oldest'],
        Field(description='Upload-date order; undated videos come last either way.'),
    ] = 'newest',
    limit: Annotated[
        int, Field(description=f'Videos per page, 1-{VIDEO_PAGE_MAX}.')
    ] = VIDEO_PAGE_DEFAULT,
    offset: Annotated[
        int, Field(description='Videos to skip: pass next_offset from the previous page.')
    ] = 0,
) -> Annotated[CallToolResult, VideoList]:
    """List the user's transcribed videos with upload date, length and transcript size.

    A page at a time, with the total. Use it to cover a channel or a period
    exhaustively, reading each video with get_transcript, or to pick media_ids for
    search_transcripts.
    """
    principal = current_principal()
    limit = max(1, min(VIDEO_PAGE_MAX, limit))
    offset = max(0, offset)
    applied = AppliedFilters()
    scope = await _resolve_library_filters(
        principal.user_id,
        applied,
        channels=channels,
        title_contains=title_contains,
        tags=tags,
        min_rating=min_rating,
        released_from=released_from,
        released_to=released_to,
    )
    if scope is None:
        return _tool_result(*build_video_list(applied, sort, 0, offset, []))

    total, rows = await md_repo.list_transcribed_media(
        principal.user_id, newest_first=sort == 'newest', offset=offset, limit=limit, **scope
    )
    videos = [
        VideoEntry(
            media_id=row.media.id,
            title=row.media.title,
            channel=row.media.channel or UNKNOWN_CHANNEL,
            uploaded=iso_date(row.media.release_timestamp),
            duration_seconds=_whole_seconds(row.media.duration or row.transcript_end),
            transcript_blocks=row.block_count,
            transcript_chars=row.text_chars,
            source_url=row.media.url,
            app_url=media_app_url(principal.base_url, row.media.id, row.media.status, 0),
        )
        for row in rows
    ]
    return _tool_result(*build_video_list(applied, sort, total, offset, videos))


def _whole_seconds(seconds: float | None) -> int | None:
    return ceil(seconds) if seconds else None


async def mention_timeline(
    terms: Annotated[
        list[str],
        Field(
            description=(
                'Words or phrases to count, e.g. ["carbon tax", "emissions trading"]. Each '
                'matches as an exact phrase with word endings ignored (launch also matches '
                'launches); a passage counts once if it has any of them. Up to '
                f'{TIMELINE_TERMS_MAX}; call once per term to compare terms.'
            )
        ),
    ],
    interval: Annotated[
        Literal['month', 'year'], Field(description='Count per upload month or per year.')
    ] = 'month',
    by_channel: Annotated[
        bool,
        Field(description='Also split each period by channel; only channels with mentions show.'),
    ] = False,
    channels: ChannelsFilter = None,
    title_contains: TitleFilter = None,
    tags: TagsFilter = None,
    min_rating: MinRatingFilter = None,
    released_from: ReleasedFromFilter = None,
    released_to: ReleasedToFilter = None,
) -> Annotated[CallToolResult, MentionTimeline]:
    """Count every transcript passage that mentions the terms, per upload period.

    For each period: matching transcript blocks, videos with a mention, and all
    transcribed videos from then for comparison. These are exhaustive full-text counts,
    not a ranked or semantic search: use them for when and how often something came
    up, then search_transcripts with a period's dates to read what was said.
    """
    principal = current_principal()
    # Quotes would only nest inside the phrase each term already is.
    phrases = [' '.join(term.replace('"', ' ').split()) for term in terms]
    phrases = list(dict.fromkeys(p for p in phrases if p))
    if not phrases:
        msg = 'terms needs at least one word or phrase.'
        raise ToolError(msg)
    if len(phrases) > TIMELINE_TERMS_MAX:
        msg = f'terms takes at most {TIMELINE_TERMS_MAX} words or phrases.'
        raise ToolError(msg)
    if any(len(p) > TIMELINE_TERM_MAX_CHARS for p in phrases):
        msg = f'Each term must be at most {TIMELINE_TERM_MAX_CHARS} characters.'
        raise ToolError(msg)

    applied = AppliedFilters()
    scope = await _resolve_library_filters(
        principal.user_id,
        applied,
        channels=channels,
        title_contains=title_contains,
        tags=tags,
        min_rating=min_rating,
        released_from=released_from,
        released_to=released_to,
    )
    matched, counts = '', []
    if scope is not None:
        matched, counts = await get_mention_timeline(
            phrases,
            interval=interval,
            by_channel=by_channel,
            user_id=principal.user_id,
            **scope,
        )
        if not matched:
            msg = 'Every term is a stop word (like "the" or "and"), so nothing can be counted.'
            raise ToolError(msg)

    timeline = MentionTimeline(
        applied=applied,
        terms=phrases,
        matched_query=matched,
        interval=interval,
        by_channel=by_channel,
        total_blocks=sum(c.blocks for c in counts),
        total_videos_with_mentions=sum(c.videos_with_mentions for c in counts),
        total_videos=sum(c.videos for c in counts),
        rows=[
            MentionRow(
                period=_period_label(c.period, interval),
                channel=c.channel,
                blocks=c.blocks,
                videos_with_mentions=c.videos_with_mentions,
                videos=c.videos,
            )
            for c in counts
            if c.blocks or not by_channel
        ],
    )
    return _tool_result(*build_mention_timeline(timeline))


def _period_label(period: datetime | None, interval: str) -> str:
    if period is None:
        return 'undated'
    return f'{period:%Y}' if interval == 'year' else f'{period:%Y-%m}'


async def search_transcripts(
    query: Annotated[
        str | None,
        Field(
            description=(
                'What to find, in natural language. It is embedded for semantic search, so '
                'phrase it like the passage you hope to find. Optional when keywords is '
                'given: without it the search is pure full-text.'
            )
        ),
    ] = None,
    keywords: Annotated[
        str | None,
        Field(
            description=(
                'Exact words or names, matched by full-text search: word endings are '
                'ignored and any of them matches. Wrap several words in double quotes to '
                'match only that exact phrase, e.g. "carbon tax" levy. With query they '
                'boost passages containing them; alone they find only such passages, '
                'every one of them, page by page.'
            )
        ),
    ] = None,
    channels: ChannelsFilter = None,
    title_contains: TitleFilter = None,
    tags: TagsFilter = None,
    min_rating: MinRatingFilter = None,
    released_from: ReleasedFromFilter = None,
    released_to: ReleasedToFilter = None,
    media_ids: Annotated[
        list[int] | None,
        Field(
            description=(
                'Only these videos, by media_id from list_videos or earlier results: '
                f'search inside a shortlist. Up to {MEDIA_IDS_MAX}.'
            )
        ),
    ] = None,
    limit: Annotated[int, Field(description='Passages per page, 1-40.')] = SEARCH_DEFAULT_LIMIT,
    offset: Annotated[
        int,
        Field(
            description=(
                'Passages to skip: pass next_offset from the previous page to read further '
                'down the same ranking.'
            )
        ),
    ] = 0,
) -> Annotated[CallToolResult, SearchResult]:
    """Search what was said in the user's videos.

    Returns timestamped passages, best first, each with title, channel, upload date,
    a source link and an in-app link, a page at a time. Cite passages by their
    [B<block_id>].
    """
    principal = current_principal()
    query = (query or '').strip() or None
    keywords = (keywords or '').strip() or None
    if query is None and keywords is None:
        msg = 'Give query, keywords, or both.'
        raise ToolError(msg)
    limit = max(1, min(SEARCH_MAX_LIMIT, limit))
    offset = max(0, offset)
    shortlist = sorted(set(media_ids or [])) or None
    if shortlist and len(shortlist) > MEDIA_IDS_MAX:
        msg = f'media_ids takes at most {MEDIA_IDS_MAX} ids; split the shortlist.'
        raise ToolError(msg)

    if query is None:
        mode, semantic_weight = 'keyword', 0.0
    elif keywords:
        mode, semantic_weight = 'hybrid', HYBRID_SEMANTIC_WEIGHT
    else:
        mode, semantic_weight = 'semantic', 1.0
    applied = AppliedSearch(
        query=query,
        keywords=keywords,
        mode=mode,
        embedding_model=None if mode == 'keyword' else settings.embedding.model,
        media_ids=shortlist,
    )
    scope = await _resolve_library_filters(
        principal.user_id,
        applied,
        channels=channels,
        title_contains=title_contains,
        tags=tags,
        min_rating=min_rating,
        released_from=released_from,
        released_to=released_to,
    )
    if scope is None:
        return _tool_result(*build_search_result(applied, [], limit, principal.base_url))

    hits = await get_hybrid_search_results(
        _embedder_factory(),
        query or '',
        None,
        semantic_weight=semantic_weight,
        limit=SEARCH_FETCH_DEPTH,
        user_id=principal.user_id,
        rating_user_id=principal.user_id,
        fts_text=keywords,
        media_ids=shortlist,
        **scope,
    )
    if not hits and mode == 'keyword' and not await describe_fts_query(keywords):
        applied.notes.append('Every keyword is a stop word, so nothing could match.')
    result, text = build_search_result(
        applied,
        merge_adjacent_hits(hits),
        limit,
        principal.base_url,
        offset=offset,
        more_beyond_depth=len(hits) >= SEARCH_FETCH_DEPTH,
    )
    return _tool_result(result, text)


async def get_transcript(
    media_id: Annotated[
        int, Field(description='media_id from a search result or library_overview.')
    ],
    start_seconds: Annotated[
        float | None,
        Field(
            description=(
                'Where to start reading; a little before a passage gives context. '
                'Default: the beginning.'
            )
        ),
    ] = None,
    end_seconds: Annotated[
        float | None,
        Field(description='Where to stop. Default: as far as max_chars allows.'),
    ] = None,
    max_chars: Annotated[
        int,
        Field(
            description=(
                'Most transcript text to return, 500-20000. Use 20000 to read a whole '
                'video in as few pages as possible.'
            )
        ),
    ] = TRANSCRIPT_DEFAULT_CHARS,
) -> Annotated[CallToolResult, TranscriptWindow]:
    """Read a video's transcript with timestamps, around a moment or page by page."""
    principal = current_principal()
    media = await md_repo.get_media_details_by_id(media_id)
    # One message for "missing" and "not yours", so ids cannot be probed.
    unavailable = f'No transcript available for media {media_id}.'
    if media is None or not await ma_repo.user_can_access_media(principal.user_id, media):
        raise ToolError(unavailable)

    start = max(0.0, start_seconds or 0.0)
    end = end_seconds
    if end is not None and end <= start:
        msg = 'end_seconds must be after start_seconds.'
        raise ToolError(msg)
    max_chars = max(TRANSCRIPT_MIN_CHARS, min(TRANSCRIPT_MAX_CHARS, max_chars))

    blocks = await tb_repo.get_blocks_in_window(media.id, start, end, TRANSCRIPT_BLOCK_FETCH)
    if not blocks:
        raise ToolError(unavailable if start == 0 else f'No transcript after {start:g}s.')

    segments: list[TranscriptSegment] = []
    used = 0
    next_start: int | None = None
    for block in blocks:
        text = ' '.join((block.text or '').split())
        if segments and used + len(text) > max_chars:
            next_start = int(block.start_time or 0)
            break
        start = block.start_time or 0
        segments.append(
            TranscriptSegment(
                block_id=block.id,
                start_seconds=int(start),
                end_seconds=ceil(block.end_time or 0),
                timestamp=format_timestamp(start),
                text=text,
                source_url=source_url(media.url, start),
                app_url=media_app_url(principal.base_url, media.id, media.status, start),
            )
        )
        used += len(text)
    else:
        following = await tb_repo.get_next_block_start(media.id, blocks[-1].start_time or 0)
        if following is not None:
            next_start = int(following)

    first = segments[0].start_seconds
    window = TranscriptWindow(
        media_id=media.id,
        title=media.title,
        channel=media.channel or UNKNOWN_CHANNEL,
        uploaded=iso_date(media.release_timestamp),
        source_url=source_url(media.url, first),
        app_url=media_app_url(principal.base_url, media.id, media.status, first),
        segments=segments,
        next_start_seconds=next_start,
    )
    return _tool_result(window, render_transcript(window))


def research() -> str:
    """Answer a question from the transcripts, with citations and an evidence table."""
    return RESEARCH_PROMPT


def build_mcp_server(
    embedder_factory: Callable[[], OnnxEmbedder] = load_embedding_model,
) -> MCPServer:
    """Build the server and its HTTP session manager, ready for `session_manager.run()`."""
    global _embedder_factory
    _embedder_factory = embedder_factory

    # MCPServer's constructor calls logging.basicConfig(). The root logger has no
    # handlers here (the app logs through its own propagating `app` logger), so that
    # call would attach one and print every app log line twice.
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        server = MCPServer('yt-hoarder', instructions=INSTRUCTIONS, log_level='WARNING')
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)

    for tool in (
        library_overview,
        list_videos,
        search_transcripts,
        mention_timeline,
        get_transcript,
    ):
        server.tool(annotations=READ_ONLY)(tool)
    server.prompt(title='Research the transcript library')(research)

    server.streamable_http_app(
        streamable_http_path='/',
        # Required, not a tuning choice: a stateful session's server task starts in the
        # first request's context, so every later request on that session id would run
        # its tools as whoever opened it. Stateless starts a task per request.
        stateless_http=True,
        json_response=True,
        # The SDK's default Host allowlist is localhost-only, which 421s every LAN or
        # proxied address. Rebinding attacks need a browser to carry ambient credentials;
        # this endpoint takes only an explicit bearer token, and prod sends no CORS.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return server
