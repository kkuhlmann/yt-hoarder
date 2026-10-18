"""The MCP server: read-only search over the caller's transcripts, plus a research prompt.

Every tool acts as the principal the HTTP endpoint authenticated, and sees what that
user sees in the app without admin view: their own media and media shared with them.
"""

import difflib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from math import ceil
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from config import settings
from mcp_server.context import current_principal
from mcp_server.formatting import (
    AppliedSearch,
    ChannelSummary,
    LibraryOverview,
    SearchResult,
    TagSummary,
    TranscriptSegment,
    TranscriptWindow,
    VideoSummary,
    build_search_result,
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
from services.transcript import get_hybrid_search_results
from utils import load_embedding_model

SEARCH_DEFAULT_LIMIT = 15
SEARCH_MAX_LIMIT = 40
# Hybrid semantic/keyword balance when keywords are given; the Downloads slider's midpoint.
HYBRID_SEMANTIC_WEIGHT = 0.5
TOP_CHANNELS = 25
RECENT_VIDEOS = 30
TRANSCRIPT_DEFAULT_CHARS = 8_000
TRANSCRIPT_MIN_CHARS = 500
TRANSCRIPT_MAX_CHARS = 20_000
TRANSCRIPT_DEFAULT_WINDOW_SECONDS = 300
# ~2.8 h of 20 s blocks: one read never needs more, and max_chars stops far sooner.
TRANSCRIPT_BLOCK_FETCH = 500
CHANNEL_FUZZY_CUTOFF = 0.75

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = """\
Search the user's personal video library (YouTube downloads) by what was said in it. \
Use these tools to research what channels said about a topic, over a time range, with \
citations.

1. library_overview: channels, upload-date span and tags. Call it first when the \
question names a channel or a period, to get exact channel names.
2. search_transcripts: timestamped passages, best first. Phrase `query` like the passage \
you hope to find; put exact names or terms in `keywords`. Run several searches with \
different phrasings for broad questions.
3. get_transcript: read around a passage (start a little before its timestamp) to quote \
it accurately, or read a whole video in pages.

When answering: cite every claim with the passage's linked id exactly as given, \
e.g. [B1234](link), and give each cited passage's upload date. End with an evidence table: \
Uploaded | Channel | Video | Time (source link / app link) | Quote. Group findings \
chronologically when the question spans a period. Only state what the passages say."""

RESEARCH_PROMPT = """\
Research my video transcript library to answer the question I ask next.

- Start with library_overview if I mention a channel, a tag or a time period.
- Run several search_transcripts calls with different phrasings; use keywords for exact \
names. Narrow with channels and released_from / released_to when I give them.
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


# Swapped by build_mcp_server, so tests can stand in for the ONNX model.
_embedder_factory: Callable[[], OnnxEmbedder] = load_embedding_model


async def library_overview(
    channel: Annotated[
        str | None,
        Field(description="Also list this channel's most recent videos."),
    ] = None,
) -> Annotated[CallToolResult, LibraryOverview]:
    """What the transcript library holds: channels, upload-date span and tags.

    Call it first to pick exact channel names and dates for search_transcripts.
    """
    principal = current_principal()
    channel_stats = await md_repo.get_transcribed_channels(principal.user_id)
    tags = await tags_repo.get_user_tags(principal.user_id)
    dated = [c for c in channel_stats if c.first_release is not None]
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
            for c in channel_stats[:TOP_CHANNELS]
        ],
        tags=[TagSummary(name=t['name'], videos=t['usage_count']) for t in tags],
    )

    if channel and channel.strip():
        matched, _ = resolve_channels([channel], [c.channel for c in channel_stats])
        if not matched:
            known = ', '.join(c.channel for c in channel_stats[:10]) or 'none'
            msg = f'No channel matching "{channel}". Channels include: {known}.'
            raise ToolError(msg)
        overview.channel = matched[0]
        media = await md_repo.get_recent_transcribed_media(
            principal.user_id, matched[0], RECENT_VIDEOS
        )
        overview.recent_videos = [
            VideoSummary(
                media_id=m.id,
                title=m.title,
                uploaded=iso_date(m.release_timestamp),
                source_url=m.url,
                app_url=media_app_url(principal.base_url, m.id, m.status, 0),
            )
            for m in media
        ]
    return _tool_result(overview, render_overview(overview))


async def search_transcripts(
    query: Annotated[
        str,
        Field(
            description=(
                'What to find, in natural language. It is embedded for semantic '
                'search, so phrase it like the passage you hope to find.'
            )
        ),
    ],
    keywords: Annotated[
        str | None,
        Field(description='Exact words or names to boost via full-text search.'),
    ] = None,
    channels: Annotated[
        list[str] | None,
        Field(description='Only these channels; close spellings are matched.'),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(description="Only media with any of these of the user's tags."),
    ] = None,
    min_rating: Annotated[
        int | None, Field(description='Only media the user rated at least this, 1-5.')
    ] = None,
    released_from: Annotated[
        str | None, Field(description='Earliest upload date: YYYY, YYYY-MM or YYYY-MM-DD.')
    ] = None,
    released_to: Annotated[
        str | None,
        Field(description='Latest upload date, inclusive: YYYY, YYYY-MM or YYYY-MM-DD.'),
    ] = None,
    limit: Annotated[int, Field(description='Passages to return, 1-40.')] = SEARCH_DEFAULT_LIMIT,
) -> Annotated[CallToolResult, SearchResult]:
    """Search what was said in the user's videos.

    Returns timestamped passages, best first, each with title, channel, upload date,
    a source link and an in-app link. Cite passages by their [B<block_id>].
    """
    principal = current_principal()
    query = query.strip()
    if not query:
        msg = 'query must not be empty.'
        raise ToolError(msg)
    keywords = (keywords or '').strip() or None
    limit = max(1, min(SEARCH_MAX_LIMIT, limit))
    if min_rating is not None and not 1 <= min_rating <= 5:
        msg = 'min_rating must be between 1 and 5.'
        raise ToolError(msg)
    date_from = parse_date_bound(released_from, end=False) if released_from else None
    date_to = parse_date_bound(released_to, end=True) if released_to else None
    if date_from and date_to and date_from >= date_to:
        msg = 'released_from must not be after released_to.'
        raise ToolError(msg)

    applied = AppliedSearch(
        query=query,
        keywords=keywords,
        mode='hybrid' if keywords else 'semantic',
        embedding_model=settings.embedding.model,
        channels=None,
        tags=None,
        min_rating=min_rating,
        released_from=date_from.date().isoformat() if date_from else None,
        released_to=(date_to - timedelta(days=1)).date().isoformat() if date_to else None,
    )
    if date_from or date_to:
        applied.notes.append('Videos with no known upload date are excluded by a date range.')

    requested_channels = _non_blank(channels)
    resolved_channels: list[str] | None = None
    if requested_channels:
        known = [c.channel for c in await md_repo.get_transcribed_channels(principal.user_id)]
        resolved_channels, applied.unmatched_channels = resolve_channels(requested_channels, known)
        applied.channels = resolved_channels

    requested_tags = _non_blank(tags)
    tag_ids: list[int] | None = None
    if requested_tags:
        found = await tags_repo.find_tags_by_name(principal.user_id, requested_tags)
        tag_ids = [t.id for t in found]
        found_names = {t.name.lower() for t in found}
        applied.tags = sorted(t.name for t in found)
        applied.unmatched_tags = [
            t.strip() for t in requested_tags if t.strip().lower() not in found_names
        ]

    if (requested_channels and not resolved_channels) or (requested_tags and not tag_ids):
        # Dropping a filter that matched nothing would answer a different question.
        applied.notes.append('Nothing was searched: a requested filter matched nothing.')
        return _tool_result(*build_search_result(applied, [], limit, principal.base_url))

    # Over-fetch: merging adjacent blocks folds several hits into one passage.
    fetch_limit = limit * 2
    hits = await get_hybrid_search_results(
        _embedder_factory(),
        query,
        None,
        semantic_weight=HYBRID_SEMANTIC_WEIGHT if keywords else 1.0,
        limit=fetch_limit,
        user_id=principal.user_id,
        rating_user_id=principal.user_id,
        tag_ids=tag_ids,
        min_rating=min_rating,
        channels=resolved_channels,
        released_from=date_from,
        released_to=date_to,
        fts_text=keywords,
    )
    result, text = build_search_result(
        applied,
        merge_adjacent_hits(hits),
        limit,
        principal.base_url,
        more_available=len(hits) >= fetch_limit,
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
        Field(
            description=(
                'Where to stop. Default: 5 minutes after start_seconds, or as far as '
                'max_chars allows when reading from the beginning.'
            )
        ),
    ] = None,
    max_chars: Annotated[
        int, Field(description='Most transcript text to return, up to 20000.')
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
    if end is None and start_seconds is not None:
        end = start + TRANSCRIPT_DEFAULT_WINDOW_SECONDS
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
        following = await tb_repo.get_blocks_in_window(media.id, blocks[-1].end_time or 0, None, 1)
        if following:
            next_start = int(following[0].start_time or 0)

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
        server = MCPServer('ytdl-hoarder', instructions=INSTRUCTIONS, log_level='WARNING')
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)

    for tool in (library_overview, search_transcripts, get_transcript):
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
