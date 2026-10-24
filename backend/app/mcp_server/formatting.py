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


class AppliedFilters(BaseModel):
    """The library filters a tool actually applied, after resolving names and dates."""

    channels: list[str] | None = None
    title_contains: str | None = None
    tags: list[str] | None = None
    min_rating: int | None = None
    released_from: str | None = None
    released_to: str | None = None
    unmatched_channels: list[str] = []
    unmatched_tags: list[str] = []
    notes: list[str] = []


class AppliedSearch(AppliedFilters):
    query: str | None
    keywords: str | None
    mode: Literal['semantic', 'hybrid', 'keyword']
    embedding_model: str | None
    media_ids: list[int] | None = None


class SearchResult(BaseModel):
    applied: AppliedSearch
    results: list[TranscriptHit]
    offset: int = 0
    next_offset: int | None = None
    passages_found: int = 0
    more_beyond_depth: bool = False
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
    # Keywords print bare: they may carry their own "quoted phrases".
    if applied.mode == 'keyword':
        head = f'Searched (keyword, full-text): keywords {applied.keywords}'
    else:
        head = f'Searched ({applied.mode}, {applied.embedding_model}): "{applied.query}"'
        if applied.keywords:
            head += f' + keywords {applied.keywords}'
    # Only the count: echoing the ids back would say which of them the user can see.
    extra = [f'media: {len(applied.media_ids)} ids'] if applied.media_ids is not None else []
    return [head, *filter_lines(applied, extra)]


def filter_lines(applied: AppliedFilters, extra: list[str] | None = None) -> list[str]:
    """The `Filters:` line plus what failed to resolve and any notes."""
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
    filters.extend(extra or [])
    lines = ['Filters: ' + ('; '.join(filters) if filters else 'none (whole library)')]
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
    offset: int = 0,
    more_beyond_depth: bool = False,
) -> tuple[SearchResult, str]:
    """Turn one page of merged passages into the result and its text.

    `spans` is the whole ranking the search fetched; the page is `spans[offset:]`, at
    most `limit` long and cut shorter by the response budget. `next_offset` counts only
    what was rendered, so a page the budget cut short continues where it stopped.
    `more_beyond_depth` says the search filled its fetch depth, so passages ranked below
    everything fetched may exist.
    """
    page = spans[offset : offset + limit]
    header = '\n'.join(_applied_lines(applied))
    footer = _answer_format(span_to_hit(page[0], base_url) if page else None)
    budget = RESPONSE_MAX_CHARS - len(header) - len(footer) - 300

    hits: list[TranscriptHit] = []
    blocks: list[str] = []
    for span in page:
        hit = span_to_hit(span, base_url)
        block = _hit_block(hit)
        # The first passage always renders, so following next_offset always advances.
        if hits and len(block) + 2 > budget:
            break
        budget -= len(block) + 2
        hits.append(hit)
        blocks.append(block)

    shown_to = offset + len(hits)
    next_offset = shown_to if shown_to < len(spans) else None
    found = f'{len(spans)}+' if more_beyond_depth else str(len(spans))

    parts = [header, '']
    if hits:
        parts.append(
            f'Passages {offset + 1}-{shown_to} of {found} found, best first. '
            'Each [B…] id links to the source at that moment:'
        )
        parts.append('\n\n'.join(blocks))
    elif spans:
        parts.append(f'No passages from offset={offset}: this search found {found}.')
    else:
        parts.append('No matching transcript passages.')
    if next_offset is not None:
        parts.append(f'Continues: offset={next_offset} shows the next passages of this search.')
    elif more_beyond_depth:
        parts.append(
            'That is as deep as this search ranks; rephrase, add keywords or narrow the '
            'filters to reach other passages.'
        )
    elif hits and offset:
        parts.append('That is every passage this search found.')
    parts.extend(['', footer])
    result = SearchResult(
        applied=applied,
        results=hits,
        offset=offset,
        next_offset=next_offset,
        passages_found=len(spans),
        more_beyond_depth=more_beyond_depth,
        truncated=next_offset is not None or more_beyond_depth,
    )
    return result, '\n'.join(parts)


# --- library_overview ---


class ChannelSummary(BaseModel):
    name: str
    videos: int
    first_upload: str | None
    last_upload: str | None


class TagSummary(BaseModel):
    name: str
    videos: int


class LibraryOverview(BaseModel):
    videos_with_transcripts: int
    first_upload: str | None
    last_upload: str | None
    undated_videos: int
    channel_count: int
    channels: list[ChannelSummary]
    tags: list[TagSummary]
    channel_offset: int = 0
    next_channel_offset: int | None = None


def render_overview(overview: LibraryOverview) -> str:
    lines = [
        f'{overview.videos_with_transcripts} videos with transcripts across '
        f'{overview.channel_count} channels, uploaded '
        f'{overview.first_upload or "?"} to {overview.last_upload or "?"}'
        + (f' ({overview.undated_videos} undated)' if overview.undated_videos else '')
        + '.',
    ]
    if overview.channels:
        first = overview.channel_offset + 1
        last = overview.channel_offset + len(overview.channels)
        whole = first == 1 and last == overview.channel_count
        lines.append('')
        lines.append(
            'Channels (videos, upload span)'
            + ('' if whole else f', {first}-{last} of {overview.channel_count}, largest first')
            + ':'
        )
        lines.extend(
            f'- {c.name}: {c.videos}, {c.first_upload or "?"} to {c.last_upload or "?"}'
            for c in overview.channels
        )
    elif overview.channel_offset:
        lines.append(f'No channels from channel_offset={overview.channel_offset}.')
    if overview.next_channel_offset is not None:
        lines.append(f'More channels: channel_offset={overview.next_channel_offset}.')
    if overview.tags:
        lines.append('')
        lines.append('Your tags: ' + ', '.join(f'{t.name} ({t.videos})' for t in overview.tags))
    lines.append('')
    lines.append(
        'Pass exact channel names and YYYY-MM-DD dates from this overview to search_transcripts, '
        "list_videos and mention_timeline; list_videos(channels=[…]) lists a channel's videos."
    )
    return '\n'.join(lines)


# --- list_videos ---


class VideoEntry(BaseModel):
    media_id: int
    title: str | None
    channel: str
    uploaded: str | None
    duration_seconds: int | None
    transcript_blocks: int
    transcript_chars: int
    source_url: str | None
    app_url: str | None


class VideoList(BaseModel):
    applied: AppliedFilters
    sort: Literal['newest', 'oldest']
    total: int
    offset: int
    videos: list[VideoEntry]
    next_offset: int | None


def _approx_chars(chars: int) -> str:
    return f'~{chars / 1000:.1f}k chars' if chars >= 1000 else f'{chars} chars'


def _video_line(video: VideoEntry) -> str:
    length = format_timestamp(video.duration_seconds) if video.duration_seconds else 'length ?'
    line = (
        f'- media {video.media_id}: "{video.title or "Untitled"}" — {video.channel} — '
        f'uploaded {video.uploaded or "unknown"} — {length} — '
        f'{video.transcript_blocks} blocks, {_approx_chars(video.transcript_chars)}'
    )
    return line + (f' — {video.source_url}' if video.source_url else '')


def build_video_list(
    applied: AppliedFilters,
    sort: Literal['newest', 'oldest'],
    total: int,
    offset: int,
    videos: list[VideoEntry],
) -> tuple[VideoList, str]:
    """One page of videos and its text, within the response budget.

    Like search pages, `next_offset` counts only the videos that fit, and the first
    one always does.
    """
    footer = (
        'get_transcript(media_id) reads a video from its start; '
        'search_transcripts(media_ids=[…]) searches inside a shortlist.'
    )
    header = filter_lines(applied)
    budget = RESPONSE_MAX_CHARS - sum(len(line) + 1 for line in header) - len(footer) - 300
    shown: list[VideoEntry] = []
    lines: list[str] = []
    for video in videos:
        line = _video_line(video)
        if shown and len(line) + 1 > budget:
            break
        budget -= len(line) + 1
        shown.append(video)
        lines.append(line)

    shown_to = offset + len(shown)
    next_offset = shown_to if shown_to < total else None
    parts = [*header, '']
    if shown:
        parts.append(f'Videos {offset + 1}-{shown_to} of {total}, {sort} first:')
        parts.extend(lines)
    elif total:
        parts.append(f'No videos from offset={offset}: {total} match.')
    else:
        parts.append('No transcribed videos match.')
    if next_offset is not None:
        parts.append(f'Continues: offset={next_offset} lists the next videos.')
    parts.extend(['', footer])
    result = VideoList(
        applied=applied,
        sort=sort,
        total=total,
        offset=offset,
        videos=shown,
        next_offset=next_offset,
    )
    return result, '\n'.join(parts)


# --- mention_timeline ---


class MentionRow(BaseModel):
    period: str
    channel: str | None
    blocks: int
    videos_with_mentions: int
    videos: int


class MentionTimeline(BaseModel):
    applied: AppliedFilters
    terms: list[str]
    matched_query: str
    interval: Literal['month', 'year']
    by_channel: bool
    total_blocks: int
    total_videos_with_mentions: int
    total_videos: int
    rows: list[MentionRow]
    rows_omitted: int = 0


def keywords_for(terms: list[str]) -> str:
    """The search_transcripts keywords that match exactly what `terms` were counted by."""
    return ' '.join(f'"{term}"' if ' ' in term else term for term in terms)


def _search_hint(timeline: MentionTimeline) -> str | None:
    dated = [row for row in timeline.rows if row.period != 'undated' and row.blocks]
    if not dated:
        return None
    busiest = max(dated, key=lambda row: row.blocks).period
    return (
        f"To read a period: search_transcripts(keywords='{keywords_for(timeline.terms)}', "
        f"released_from='{busiest}', released_to='{busiest}')."
    )


def build_mention_timeline(timeline: MentionTimeline) -> tuple[MentionTimeline, str]:
    """The timeline's text, its table cut to the response budget.

    The totals were summed over every row before any is cut, so they stay exact.
    """
    names = ' or '.join(f'"{term}"' for term in timeline.terms)
    head = f'Mentions of {names} by upload {timeline.interval}'
    if timeline.matched_query:
        head += f' (full text: {timeline.matched_query}; counts transcript blocks of ~20 s)'
    parts = [head + '.', *filter_lines(timeline.applied)]
    if not timeline.total_blocks:
        parts.append(f'No passage mentions them in {timeline.total_videos} videos.')
        return timeline, '\n'.join(parts)

    parts.append(
        f'{timeline.total_blocks} matching blocks in {timeline.total_videos_with_mentions} '
        f'of {timeline.total_videos} videos.'
    )
    columns = [timeline.interval.capitalize()]
    if timeline.by_channel:
        columns.append('Channel')
    columns += ['Blocks', 'Videos with a mention', 'Videos']
    hint = _search_hint(timeline)
    parts += ['', ' | '.join(columns)]
    budget = RESPONSE_MAX_CHARS - sum(len(p) + 1 for p in parts) - len(hint or '') - 300
    kept: list[MentionRow] = []
    for row in timeline.rows:
        cells = [row.period, *([_cell(row.channel or '')] if timeline.by_channel else [])]
        line = ' | '.join([*cells, str(row.blocks), str(row.videos_with_mentions), str(row.videos)])
        if len(line) + 1 > budget:
            break
        budget -= len(line) + 1
        kept.append(row)
        parts.append(line)
    omitted = len(timeline.rows) - len(kept)
    if omitted:
        parts.append(
            f"{omitted} more rows did not fit; use interval='year', fewer channels or a "
            'narrower date range.'
        )
    if hint:
        parts += ['', hint]
    return timeline.model_copy(update={'rows': kept, 'rows_omitted': omitted}), '\n'.join(parts)


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
