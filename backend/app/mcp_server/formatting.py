"""Result models for the MCP tools, and the compact text the model actually reads.

Each tool returns both: the model below as structured content (validated against the
published output schema), and a rendered text block, because `content` is the only part
of a tool result most agents put in front of the model and the SDK's default for it is
indented JSON, which spends tokens on punctuation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
from typing import Literal

from pydantic import BaseModel

from mcp_server.links import app_url, format_timestamp, source_url
from models import TaskStatus
from repositories.media_details import UNKNOWN_CHANNEL

EXCERPT_MAX_CHARS = 900
# Claude Code warns above ~10k tokens of tool output and truncates at 25k.
RESPONSE_MAX_CHARS = 20_000
# Blocks are ~20 s and contiguous, so neighbouring hits share an edge; the slack
# absorbs whole-second rounding.
MERGE_GAP_SECONDS = 5.0
MERGE_MAX_BLOCKS = 3


def iso_date(value: datetime | None) -> str | None:
    return value.date().isoformat() if value else None


def clip_text(text: str, limit: int) -> str:
    text = ' '.join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(' ', 1)[0] + ' …'


def _md_url(url: str) -> str:
    # A space or ")" would end a Markdown link target early.
    return url.replace(' ', '%20').replace('(', '%28').replace(')', '%29')


def citation(block_id: int, source_url: str | None, app_url: str | None) -> str:
    """How a passage is cited: its id, linked to the moment it was said.

    The link rides inside the id because models copy a citation token as they saw
    it, so an inline citation carries its evidence without a separate instruction.
    The source wins over the in-app link: it opens anywhere, not just on a device
    that can reach this server.
    """
    url = source_url or app_url
    return f'[B{block_id}]({_md_url(url)})' if url else f'[B{block_id}]'


def _cell(text: str) -> str:
    return text.replace('|', '\\|')


# --- search_transcripts ---


class TranscriptHit(BaseModel):
    block_id: int
    media_id: int
    title: str | None
    channel: str
    uploaded: str | None
    start_seconds: int
    end_seconds: int
    timestamp: str
    text: str
    score: float
    source_url: str | None
    app_url: str | None


class AppliedSearch(BaseModel):
    query: str
    keywords: str | None
    mode: Literal['semantic', 'hybrid']
    embedding_model: str
    channels: list[str] | None
    tags: list[str] | None
    min_rating: int | None
    released_from: str | None
    released_to: str | None
    title_contains: str | None = None
    unmatched_channels: list[str] = []
    unmatched_tags: list[str] = []
    notes: list[str] = []


class SearchResult(BaseModel):
    applied: AppliedSearch
    results: list[TranscriptHit]
    truncated: bool = False


@dataclass
class _Span:
    media: dict
    rank: int
    start: float
    end: float
    block_ids: list[int] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    score: float = 0.0


def merge_adjacent_hits(hits: list[dict]) -> list[_Span]:
    """Fold hits that sit next to each other in the same video into one passage.

    Ordered by the best-ranked hit in each passage, which is the search's own order
    (RRF in hybrid mode), not by display similarity. Reads the hits without touching
    them: they may be the search cache's own objects.
    """
    by_media: dict[int, list[tuple[int, dict]]] = {}
    for rank, hit in enumerate(hits):
        by_media.setdefault(hit['media_details']['id'], []).append((rank, hit))

    spans: list[_Span] = []
    for media_hits in by_media.values():
        current: _Span | None = None
        for rank, hit in sorted(media_hits, key=lambda item: item[1]['start_time'] or 0.0):
            start = hit['start_time'] or 0.0
            end = hit['end_time'] or start
            if (
                current is not None
                and start - current.end <= MERGE_GAP_SECONDS
                and len(current.block_ids) < MERGE_MAX_BLOCKS
            ):
                current.end = max(current.end, end)
                current.rank = min(current.rank, rank)
            else:
                current = _Span(media=hit['media_details'], rank=rank, start=start, end=end)
                spans.append(current)
            current.block_ids.append(hit['transcript_block_id'])
            current.texts.append(hit['text'] or '')
            current.score = max(current.score, hit['similarity'])

    spans.sort(key=lambda span: span.rank)
    return spans


def span_to_hit(span: _Span, base_url: str) -> TranscriptHit:
    media = span.media
    return TranscriptHit(
        block_id=span.block_ids[0],
        media_id=media['id'],
        title=media['title'],
        channel=media['channel'] or UNKNOWN_CHANNEL,
        uploaded=iso_date(media.get('release_timestamp')),
        start_seconds=int(span.start),
        end_seconds=ceil(span.end),
        timestamp=format_timestamp(span.start),
        text=clip_text(' '.join(span.texts), EXCERPT_MAX_CHARS),
        score=round(span.score, 3),
        source_url=source_url(media['url'], span.start),
        app_url=media_app_url(base_url, media['id'], media['status'], span.start),
    )


def media_app_url(base_url: str, media_id: int, status: str, seconds: float) -> str | None:
    # Only a COMPLETE item has somewhere to land in the app: a downloaded file plays, and a
    # transcript-only item opens its transcript viewer. Anything else would open the
    # library and toast that the media is unavailable.
    if status != TaskStatus.COMPLETE:
        return None
    return app_url(base_url, media_id, seconds)


def _hit_block(hit: TranscriptHit) -> str:
    lines = [
        f'{citation(hit.block_id, hit.source_url, hit.app_url)} "{hit.title or "Untitled"}" — '
        f'{hit.channel} — uploaded {hit.uploaded or "unknown"} — {hit.timestamp} '
        f'(media {hit.media_id}, score {hit.score})'
    ]
    # Without a source the citation already links the app, so it isn't repeated.
    if hit.source_url and hit.app_url:
        lines.append(f'app: {hit.app_url}')
    lines.append(hit.text)
    return '\n'.join(lines)


def _evidence_time_cell(hit: TranscriptHit) -> str:
    links = []
    if hit.source_url:
        links.append(f'[{hit.timestamp}]({_md_url(hit.source_url)})')
    if hit.app_url:
        links.append(f'[▶ app]({_md_url(hit.app_url)})')
    return ' · '.join(links) or hit.timestamp


def _answer_format(example: TranscriptHit | None) -> str:
    """The citation convention, shown with a real row built from the best passage.

    It sits at the end of every search result, right where the model has just read
    the passages, because instructions further away (server instructions, the
    research prompt) are easy for it to drop. Phrased as the format of these results
    rather than as an order: models rightly treat commands inside tool output warily.
    """
    lines = []
    if example is not None:
        lines += [
            'Answer format for these passages:',
            '- Cite each claim inline with its linked id exactly as shown, e.g. '
            f'{citation(example.block_id, example.source_url, example.app_url)}.',
            '- End the answer with an Evidence table of the passages you cited:',
            '| Uploaded | Channel | Video | Time | Quote |',
            '|---|---|---|---|---|',
            f'| {example.uploaded or "unknown"} | {_cell(example.channel)} | '
            f'{_cell(clip_text(example.title or "Untitled", 60))} | '
            f'{_evidence_time_cell(example)} | "{_cell(clip_text(example.text, 80))}" |',
        ]
    lines.append('get_transcript(media_id, start_seconds) reads the surrounding transcript.')
    return '\n'.join(lines)


def _applied_lines(applied: AppliedSearch) -> list[str]:
    head = f'Searched ({applied.mode}, {applied.embedding_model}): "{applied.query}"'
    if applied.keywords:
        head += f' + keywords "{applied.keywords}"'
    filters = []
    if applied.channels is not None:
        filters.append('channels: ' + (', '.join(applied.channels) or 'none matched'))
    if applied.title_contains:
        filters.append(f'title contains: {applied.title_contains}')
    if applied.tags is not None:
        filters.append('tags: ' + (', '.join(applied.tags) or 'none matched'))
    if applied.min_rating is not None:
        filters.append(f'rating ≥ {applied.min_rating}')
    if applied.released_from or applied.released_to:
        filters.append(f'uploaded {applied.released_from or "…"} to {applied.released_to or "…"}')
    lines = [head, 'Filters: ' + ('; '.join(filters) if filters else 'none (whole library)')]
    if applied.unmatched_channels:
        lines.append('No such channel: ' + ', '.join(applied.unmatched_channels))
    if applied.unmatched_tags:
        lines.append('No such tag: ' + ', '.join(applied.unmatched_tags))
    lines.extend(f'Note: {note}' for note in applied.notes)
    return lines


def build_search_result(
    applied: AppliedSearch,
    spans: list[_Span],
    limit: int,
    base_url: str,
    *,
    more_available: bool = False,
) -> tuple[SearchResult, str]:
    """Turn merged passages into the result and its text, within the response budget.

    `more_available` says the search filled its fetch limit, so passages beyond those
    fetched may exist even when merging left fewer than `limit` of them.
    """
    header = '\n'.join(_applied_lines(applied))
    footer = _answer_format(span_to_hit(spans[0], base_url) if spans[:limit] else None)
    budget = RESPONSE_MAX_CHARS - len(header) - len(footer) - 200

    hits: list[TranscriptHit] = []
    blocks: list[str] = []
    truncated = more_available or len(spans) > limit
    for span in spans[:limit]:
        hit = span_to_hit(span, base_url)
        block = _hit_block(hit)
        if len(block) + 2 > budget:
            truncated = True
            break
        budget -= len(block) + 2
        hits.append(hit)
        blocks.append(block)

    parts = [header, '']
    if hits:
        parts.append(
            f'{len(hits)} passage(s), best first. Each [B…] id links to the source at that moment:'
        )
        parts.append('\n\n'.join(blocks))
    else:
        parts.append('No matching transcript passages.')
    if truncated:
        parts.append('More passages matched; narrow the search to see others.')
    parts.extend(['', footer])
    return SearchResult(applied=applied, results=hits, truncated=truncated), '\n'.join(parts)


# --- library_overview ---


class ChannelSummary(BaseModel):
    name: str
    videos: int
    first_upload: str | None
    last_upload: str | None


class TagSummary(BaseModel):
    name: str
    videos: int


class VideoSummary(BaseModel):
    media_id: int
    title: str | None
    uploaded: str | None
    source_url: str | None
    app_url: str | None


class LibraryOverview(BaseModel):
    videos_with_transcripts: int
    first_upload: str | None
    last_upload: str | None
    undated_videos: int
    channel_count: int
    channels: list[ChannelSummary]
    tags: list[TagSummary]
    channel: str | None = None
    recent_videos: list[VideoSummary] = []


def render_overview(overview: LibraryOverview) -> str:
    lines = [
        f'{overview.videos_with_transcripts} videos with transcripts across '
        f'{overview.channel_count} channels, uploaded '
        f'{overview.first_upload or "?"} to {overview.last_upload or "?"}'
        + (f' ({overview.undated_videos} undated)' if overview.undated_videos else '')
        + '.',
    ]
    if overview.channels:
        shown = len(overview.channels)
        more = overview.channel_count - shown
        lines.append('')
        lines.append(
            'Channels (videos, upload span)'
            + (f', top {shown} of {overview.channel_count}' if more else '')
            + ':'
        )
        lines.extend(
            f'- {c.name}: {c.videos}, {c.first_upload or "?"} to {c.last_upload or "?"}'
            for c in overview.channels
        )
    if overview.tags:
        lines.append('')
        lines.append('Your tags: ' + ', '.join(f'{t.name} ({t.videos})' for t in overview.tags))
    if overview.channel is not None:
        lines.append('')
        lines.append(f'Most recent videos from {overview.channel}:')
        lines.extend(
            f'- media {v.media_id}: "{v.title or "Untitled"}", uploaded {v.uploaded or "unknown"}'
            for v in overview.recent_videos
        )
    lines.append('')
    lines.append(
        'Pass exact channel names and YYYY-MM-DD dates from this overview to search_transcripts.'
    )
    return '\n'.join(lines)


# --- get_transcript ---


class TranscriptSegment(BaseModel):
    block_id: int
    start_seconds: int
    end_seconds: int
    timestamp: str
    text: str
    source_url: str | None
    app_url: str | None


class TranscriptWindow(BaseModel):
    media_id: int
    title: str | None
    channel: str
    uploaded: str | None
    source_url: str | None
    app_url: str | None
    segments: list[TranscriptSegment]
    next_start_seconds: int | None


def render_transcript(window: TranscriptWindow) -> str:
    lines = [
        f'"{window.title or "Untitled"}" — {window.channel} — uploaded {window.uploaded or "unknown"}'
        f' (media {window.media_id})',
    ]
    if window.app_url:
        lines.append(f'app: {window.app_url}')
    lines.append('')
    lines.extend(
        f'{citation(s.block_id, s.source_url, s.app_url)} {s.timestamp} {s.text}'
        for s in window.segments
    )
    lines.append('')
    if window.next_start_seconds is not None:
        lines.append(
            f'Continues: call get_transcript with start_seconds={window.next_start_seconds}.'
        )
    lines.append('Cite these passages by their linked [B…] id, with the upload date.')
    return '\n'.join(lines)
