import contextlib
import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil, floor
from typing import Literal

from cachetools import TTLCache
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.media_details as md_repo
import repositories.settings as settings_repo
import repositories.task_records as task_repo
import repositories.transcript_blocks as tb_repo
from config import settings
from database import db
from logger import logger
from models import MediaDetails, MediaType, TaskStatus, TranscriptBlock
from progress_publisher import publish_progress
from services.embeddings import OnnxEmbedder
from ytdlp.files import transcript_staging_base
from ytdlp.subtitles import find_subtitle_file, parse_subtitle_file

# NOTE: faster_whisper is imported lazily inside the functions that use it.
# This module is imported by the API process (for search) and by task
# registration — a top-level import would load ctranslate2 in every process
# instead of only the ML child.

# TTL bounds how long a stale entry can outlive an access change: results are
# per-user but there is no invalidation on share/unshare or new transcripts, so
# without expiry a revoked user keeps cached hits until LRU eviction. Sized in hits,
# not entries: an MCP search caches a few hundred so all its pages share one entry.
semantic_cache = TTLCache(maxsize=20_000, ttl=300, getsizeof=lambda hits: max(1, len(hits)))

FFPROBE_TIMEOUT_SECONDS = 10
FFMPEG_EXTRACT_TIMEOUT_SECONDS = 600
FFMPEG_CHUNK_TIMEOUT_SECONDS = 120


class TranscriptCancelled(Exception):  # noqa: N818 — control-flow signal, not an error condition
    """Raised when a transcript task's TaskRecord.status has been set to CANCELLED."""


def _check_transcript_cancelled(task_id: str | None) -> None:
    """Poll the DB for task cancellation. Raises TranscriptCancelled if cancelled.

    Used for cooperative cancellation as a fallback to the SIGTERM the ML child
    receives on cancel: long-running work periodically calls this helper to
    notice a CANCELLED status written by the API and exit gracefully.
    """
    if not task_id:
        return
    record = task_repo.sync_get_task_by_task_id(task_id)
    if record is not None and record.status == TaskStatus.CANCELLED:
        msg = f'Task {task_id} cancelled by user'
        raise TranscriptCancelled(msg)


def _get_transcript_settings() -> tuple[int, int]:
    """Get transcript chunk and block duration from settings.

    Returns:
        Tuple of (chunk_duration, block_duration) in seconds.
    """
    settings = settings_repo.sync_get_settings()
    return settings.transcript_chunk_duration, settings.transcript_block_duration


def _get_whisper_cache_path(media_file_path: str) -> str:
    base_path = os.path.splitext(media_file_path)[0]
    return f'{base_path}.whisper.json.gz'


def _save_whisper_cache(
    media_file_path: str,
    raw_segments: list[tuple[float, float, str]],
    whisper_model: str,
) -> str | None:
    """Save raw whisper segments to a gzip-compressed JSON cache file.

    Returns the cache file path on success, None on failure.
    """
    cache_path = _get_whisper_cache_path(media_file_path)
    cache_data = {
        'version': 1,
        'source': 'whisper',
        'whisper_model': whisper_model,
        'created_at': datetime.now(UTC).isoformat(),
        'segment_count': len(raw_segments),
        'segments': [[s, e, t] for s, e, t in raw_segments],
    }
    try:
        with gzip.open(cache_path, 'wt', encoding='utf-8') as f:
            json.dump(cache_data, f)
        logger.info(f'Saved whisper cache ({len(raw_segments)} segments) to {cache_path}')
    except OSError as e:
        logger.warning(f'Failed to save whisper cache: {e}')
        return None
    else:
        return cache_path


def _load_whisper_cache(
    media_file_path: str, expected_model: str | None = None
) -> list[tuple[float, float, str]] | None:
    """Load raw whisper segments from a gzip-compressed JSON cache file.

    Returns None if the cache file is missing, corrupt, or was generated with
    a different whisper model than expected_model (stale cache).
    """
    cache_path = _get_whisper_cache_path(media_file_path)
    if not os.path.exists(cache_path):
        return None

    try:
        with gzip.open(cache_path, 'rt', encoding='utf-8') as f:
            cache_data = json.load(f)
    except (OSError, json.JSONDecodeError, EOFError) as e:
        logger.warning(f'Failed to read whisper cache {cache_path}: {e}')
        return None

    if cache_data.get('version') != 1 or cache_data.get('source') != 'whisper':
        logger.warning(f'Incompatible whisper cache format in {cache_path}')
        return None

    if expected_model and cache_data.get('whisper_model') != expected_model:
        logger.info(
            f'Whisper model changed ({cache_data.get("whisper_model")} → {expected_model}), '
            f'ignoring cache'
        )
        return None

    segments = cache_data.get('segments', [])
    logger.info(f'Using cached whisper transcript ({len(segments)} segments) from {cache_path}')
    return [(s, e, t) for s, e, t in segments]


def get_media_duration(file_path: str) -> float:
    """Get duration of media file in seconds"""
    result = subprocess.run(
        [
            'ffprobe',
            '-v',
            'error',
            '-show_entries',
            'format=duration',
            '-of',
            'default=noprint_wrappers=1:nokey=1',
            file_path,
        ],
        capture_output=True,
        text=True,
        timeout=FFPROBE_TIMEOUT_SECONDS,
    )
    stdout = result.stdout.strip()
    if result.returncode != 0 or not stdout:
        stderr_msg = result.stderr.strip() if result.stderr else 'no stderr output'
        msg = f'ffprobe failed for {file_path} (returncode={result.returncode}): {stderr_msg}'
        raise ValueError(msg)
    return float(stdout)


def extract_transcript_audio(video_path: str) -> str:
    """
    Extract low-bitrate audio optimized for transcription

    Returns:
        Path to extracted audio file
    """
    base_path = os.path.splitext(video_path)[0]
    audio_path = f'{base_path}_audio_transcript.mp3'

    subprocess.run(
        [
            'ffmpeg',
            '-i',
            video_path,
            '-vn',  # No video
            '-acodec',
            'libmp3lame',
            '-ab',
            '32k',  # 32 kbps
            '-ar',
            '16000',  # 16 kHz
            '-ac',
            '1',  # Mono
            '-y',
            audio_path,
        ],
        check=True,
        capture_output=True,
        timeout=FFMPEG_EXTRACT_TIMEOUT_SECONDS,
    )

    return audio_path


def create_audio_chunks(
    file_path: str, chunk_duration: int | None = None
) -> tuple[list[tuple[str, float]], str]:
    """
    Split audio file into chunks for memory-efficient processing.

    Args:
        file_path: Path to the audio file
        chunk_duration: Duration of each chunk in seconds (uses setting if not provided)

    Returns:
        Tuple of (list of (chunk_path, time_offset) tuples, temp_directory_path)
    """
    if chunk_duration is None:
        chunk_duration, _ = _get_transcript_settings()

    duration = get_media_duration(file_path)
    chunks = []

    temp_dir = tempfile.mkdtemp(prefix='transcript_chunks_')

    offset = 0.0
    chunk_index = 0

    while offset < duration:
        chunk_path = os.path.join(temp_dir, f'chunk_{chunk_index:03d}.mp3')

        # Copy codec for speed: audio is already optimized, no re-encoding needed
        subprocess.run(
            [
                'ffmpeg',
                '-i',
                file_path,
                '-ss',
                str(offset),
                '-t',
                str(chunk_duration),
                '-acodec',
                'copy',
                '-y',
                chunk_path,
            ],
            check=True,
            capture_output=True,
            timeout=FFMPEG_CHUNK_TIMEOUT_SECONDS,
        )

        chunks.append((chunk_path, offset))
        logger.debug(f'Created chunk {chunk_index} at offset {offset}s: {chunk_path}')

        offset += chunk_duration
        chunk_index += 1

    logger.info(f'Split {file_path} into {len(chunks)} chunks of {chunk_duration}s each')
    return chunks, temp_dir


def _aggregate_segments_into_blocks(
    raw_segments: list[tuple[float, float, str]],
    media_details_id: int,
    block_duration: int | None = None,
    transcript_model: str | None = None,
) -> list[TranscriptBlock]:
    """
    Aggregate raw segments into TranscriptBlocks based on block_duration setting.

    Args:
        raw_segments: List of (start, end, text) tuples from whisper or subtitle parsing.
        media_details_id: ID of the media this transcript belongs to.
        block_duration: Duration in seconds for grouping segments into blocks.
        transcript_model: Model name to store on blocks. Defaults to whisper model from config.
    """
    if not raw_segments:
        return []

    if block_duration is None:
        _, block_duration = _get_transcript_settings()

    transcript_blocks = []
    block_text = ''
    block_start = floor(raw_segments[0][0])
    block_end = block_start + 1
    if transcript_model is None:
        transcript_model = settings.transcription.whisper_model

    for start_time, end_time, segment_text in raw_segments:
        segment_start = floor(start_time)
        if (segment_start - block_start) > block_duration:
            # Cues overlap their successors (YouTube's roll-up captions by seconds), so a
            # block is cut off where the next one starts: blocks partition the timeline.
            transcript_blocks.append(
                TranscriptBlock(
                    text=block_text.strip(),
                    start_time=block_start,
                    end_time=min(block_end, segment_start),
                    media_details_id=media_details_id,
                    transcript_model=transcript_model,
                )
            )
            block_text = ''
            block_start = segment_start
            block_end = block_start + 1

        block_text += f' {segment_text.strip()}'
        block_end = max(block_end, ceil(end_time))

    if block_text.strip():
        transcript_blocks.append(
            TranscriptBlock(
                text=block_text.strip(),
                start_time=block_start,
                end_time=block_end,
                media_details_id=media_details_id,
                transcript_model=transcript_model,
            )
        )

    return transcript_blocks


def generate_raw_segments(
    file_path: str,
    media_id: int | None = None,
    chunk_duration: int | None = None,
    task_id: str | None = None,
    user_id: int | None = None,
) -> list[tuple[float, float, str]]:
    """Run Whisper on an audio file and return raw (start, end, text) segments.

    Handles both short files (direct processing) and long files (chunk-based).
    """
    logger.info('Generating raw whisper segments for %s', file_path)
    _check_transcript_cancelled(task_id)

    if chunk_duration is None:
        chunk_duration, _ = _get_transcript_settings()

    if not os.path.exists(file_path):
        logger.error(f'File not found: {file_path}')
        return []

    duration = get_media_duration(file_path)
    model_name = settings.transcription.whisper_model
    cpu_threads = settings.transcription.whisper_cpu_threads
    num_workers = settings.transcription.whisper_num_workers
    raw_segments: list[tuple[float, float, str]] = []

    # Check before the multi-second Whisper model load for an already-cancelled task.
    _check_transcript_cancelled(task_id)
    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_name,
        device='cpu',
        compute_type='int8',
        cpu_threads=cpu_threads,
        num_workers=num_workers,
    )

    if duration <= chunk_duration:
        logger.info(f'Short file ({duration:.1f}s), processing directly')
        # Short-file path has no mid-transcribe cancellation opportunity; check once here.
        _check_transcript_cancelled(task_id)
        segments, _ = model.transcribe(
            file_path,
            vad_filter=True,
            vad_parameters={'min_silence_duration_ms': 1000, 'speech_pad_ms': 400},
        )

        raw_segments.extend((segment.start, segment.end, segment.text) for segment in segments)

        if task_id and media_id:
            update_progress(task_id, media_id, 100.0, 'Transcription complete', user_id=user_id)

        return raw_segments

    logger.info(f'Long file ({duration:.1f}s), processing in {chunk_duration}s chunks')
    chunks, temp_dir = create_audio_chunks(file_path, chunk_duration)
    total_chunks = len(chunks)

    try:
        for chunk_index, (chunk_path, time_offset) in enumerate(chunks):
            # Cooperative cancellation point: each chunk boundary is a chance
            # to notice a CANCELLED status and bail gracefully.
            _check_transcript_cancelled(task_id)
            logger.info(f'Processing chunk {chunk_index + 1}/{total_chunks}')
            segments, _ = model.transcribe(
                chunk_path,
                vad_filter=True,
                vad_parameters={'min_silence_duration_ms': 1000, 'speech_pad_ms': 400},
            )

            raw_segments.extend(
                (segment.start + time_offset, segment.end + time_offset, segment.text)
                for segment in segments
            )

            with contextlib.suppress(OSError):
                os.remove(chunk_path)

            if task_id and media_id:
                progress = (chunk_index + 1) / total_chunks * 100
                status_msg = f'{chunk_index + 1}/{total_chunks} chunks processed'
                update_progress(task_id, media_id, progress, status_msg, user_id=user_id)

        logger.info('Completed processing all chunks')

        if task_id and media_id:
            update_progress(task_id, media_id, 100.0, 'Transcription complete', user_id=user_id)
    finally:
        try:
            shutil.rmtree(temp_dir)
        except OSError as e:
            logger.warning(f'Failed to clean up temp directory: {e}')

    return raw_segments


def update_progress(
    task_id: str,
    _media_id: int,
    progress: float,
    status_message: str | None = None,
    user_id: int | None = None,
):
    """Update transcript progress (sync) and publish via SSE.

    Args:
        user_id: Owner of the media. Required for the event to reach a non-admin
            subscriber — the SSE stream drops events with no user_id.
    """
    progress_rounded = round(progress, 2)
    logger.info(f'Transcript progress {progress_rounded}% for task {task_id}')

    task_update = {'percent_complete': int(progress_rounded)}
    if status_message:
        task_update['status_message'] = status_message
    task_repo.sync_update_one(task_id, task_update)

    published = publish_progress(task_id, task_update, user_id=user_id)
    if not published:
        logger.warning(f'Failed to publish transcript progress for task {task_id}')


def _try_external_subtitles(
    file_path: str,
    media_details_id: int,
    task_id: str | None,
    force_whisper: bool,
    force_recompute: bool,
    user_id: int | None = None,
) -> list[TranscriptBlock] | None:
    """Try to build transcript blocks from external subtitle files.

    Returns None if no subtitles found or if forced to use whisper.
    """
    if force_whisper or force_recompute:
        return None

    subtitle_path = find_subtitle_file(file_path)
    if not subtitle_path:
        return None

    raw_segments = parse_subtitle_file(subtitle_path)
    if not raw_segments:
        return None

    logger.info(f'Using external subtitles for transcript: {subtitle_path}')
    if task_id and media_details_id:
        update_progress(
            task_id,
            media_details_id,
            100.0,
            'Transcript from external subtitles',
            user_id=user_id,
        )

    model_name = (
        'yt-dlp-subtitles-json3' if subtitle_path.endswith('.json3') else 'yt-dlp-subtitles-vtt'
    )
    return _aggregate_segments_into_blocks(
        raw_segments, media_details_id, transcript_model=model_name
    )


def _try_cached_whisper(
    file_path: str,
    media_details_id: int,
    task_id: str | None,
    whisper_model_name: str,
    force_recompute: bool,
    user_id: int | None = None,
) -> list[TranscriptBlock] | None:
    """Try to build transcript blocks from a cached whisper file on disk.

    Returns None if no valid cache exists or if force_recompute is set.
    """
    if force_recompute:
        return None

    cached_segments = _load_whisper_cache(file_path, whisper_model_name)
    if not cached_segments:
        return None

    if task_id and media_details_id:
        update_progress(
            task_id,
            media_details_id,
            100.0,
            'Transcript from cached whisper file',
            user_id=user_id,
        )

    return _aggregate_segments_into_blocks(cached_segments, media_details_id)


def _run_fresh_transcription(
    file_path: str,
    media_details_id: int,
    task_id: str | None,
    whisper_model_name: str,
    user_id: int | None = None,
) -> list[TranscriptBlock]:
    audio_path = extract_transcript_audio(file_path)
    try:
        raw_segments = generate_raw_segments(
            audio_path, media_id=media_details_id, task_id=task_id, user_id=user_id
        )
    finally:
        with contextlib.suppress(OSError):
            os.remove(audio_path)

    if raw_segments:
        _save_whisper_cache(file_path, raw_segments, whisper_model_name)

    return _aggregate_segments_into_blocks(raw_segments, media_details_id)


def create_transcript_blocks(
    media_details: MediaDetails,
    task_id: str | None = None,
    force_recompute: bool = False,
) -> list[TranscriptBlock]:
    """Extract audio and generate transcript blocks for media.

    Priority order:
    1. External subtitles (unless force_whisper or force_recompute)
    2. Cached whisper segments from disk (unless force_recompute)
    3. Fresh Whisper transcription (saves cache afterward)

    Args:
        media_details: The media to transcribe.
        task_id: task ID for progress tracking.
        force_recompute: If True, delete cached whisper file and re-run Whisper.
    """
    app_settings = settings_repo.sync_get_settings()
    whisper_model_name = settings.transcription.whisper_model

    # A transcript-only item stages its captions by URL and may have no media file at all.
    source_path = media_details.file_path
    if media_details.media_type == MediaType.TRANSCRIPT and not source_path:
        source_path = transcript_staging_base(media_details.url)

    if force_recompute and media_details.file_path:
        cache_path = _get_whisper_cache_path(media_details.file_path)
        if os.path.exists(cache_path):
            try:
                os.remove(cache_path)
                logger.info(f'Deleted whisper cache for force recompute: {cache_path}')
            except OSError as e:
                logger.warning(f'Failed to delete whisper cache: {e}')

    result = _try_external_subtitles(
        source_path,
        media_details.id,
        task_id,
        app_settings.force_whisper_transcription,
        force_recompute,
        user_id=media_details.owner_id,
    )
    if result is not None:
        return result

    result = _try_cached_whisper(
        source_path,
        media_details.id,
        task_id,
        whisper_model_name,
        force_recompute,
        user_id=media_details.owner_id,
    )
    if result is not None:
        return result

    if not media_details.file_path:
        msg = f'No usable captions and no media file to transcribe for {media_details.url}'
        raise FileNotFoundError(msg)

    return _run_fresh_transcription(
        media_details.file_path,
        media_details.id,
        task_id,
        whisper_model_name,
        user_id=media_details.owner_id,
    )


def _embedding_to_pgvector_str(embedding: list) -> str:
    """Convert embedding list to pgvector string format: '[0.1,0.2,...]'"""
    return '[' + ','.join(str(x) for x in embedding) + ']'


def add_embeddings(
    transcript_blocks: list[TranscriptBlock], model: OnnxEmbedder
) -> list[TranscriptBlock]:
    """
    Generate embeddings for transcript blocks and atomically persist everything.

    Delegates all DB work (delete old → insert blocks → insert embeddings) to a
    single-transaction repository method to avoid connection churn and partial writes.
    """
    if not transcript_blocks:
        return []

    text_blocks = [tb.text for tb in transcript_blocks]
    embeddings = model.encode(text_blocks, normalize_embeddings=True)
    embedding_model_name = settings.embedding.model

    for block in transcript_blocks:
        block.embedding_model = embedding_model_name

    media_details_id = transcript_blocks[0].media_details_id

    embeddings_data = [
        {'embedding': _embedding_to_pgvector_str(emb.tolist())} for emb in embeddings
    ]

    tb_repo.sync_replace_transcript_blocks_with_embeddings(
        media_details_id, transcript_blocks, embeddings_data
    )

    return transcript_blocks


# --- Async functions for FastAPI routes ---


def _quote_tsquery_term(term: str) -> str:
    escaped = term.replace('\\', '\\\\').replace("'", "''")
    return f"'{escaped}'"


# A double-quoted run is one alternative; anything else splits on whitespace. A quote
# with no partner never opens a phrase, it just stays on its word as plain text.
_FTS_TOKEN = re.compile(r'"([^"]*)"|(\S+)')


def fts_any_of(alternatives: Iterable[str]) -> str:
    """A tsquery input matching any of `alternatives`, multi-word ones as exact phrases.

    Each alternative is wrapped as one quoted lexeme because to_tsquery parses
    operator syntax: an unquoted '!' or "'" out of ordinary prose is a syntax error
    that fails the whole statement. Backslash must be escaped too, and before the
    apostrophe — inside a quoted lexeme it escapes the next character, so a term
    ending in one would swallow its own closing quote. A quoted lexeme holding several
    words is how to_tsquery spells a phrase: `'carbon tax'` parses to
    `'carbon' <-> 'tax'`.
    """
    return ' | '.join(_quote_tsquery_term(a) for a in alternatives if a.strip())


def split_fts_terms(search_query: str) -> list[str]:
    """The alternatives in a keyword string: "double-quoted phrases" and bare words."""
    return [phrase or word for phrase, word in _FTS_TOKEN.findall(search_query)]


def _build_fts_query(search_query: str) -> str:
    """Convert a search string into an OR-based tsquery input.

    Documents matching ANY bare word or "quoted phrase" are returned, with ts_rank_cd
    ranking multi-term matches higher.
    """
    return fts_any_of(split_fts_terms(search_query))


@dataclass(frozen=True)
class MentionCount:
    period: datetime | None
    channel: str | None
    videos: int
    videos_with_mentions: int
    blocks: int


async def get_mention_timeline(
    terms: list[str],
    *,
    interval: Literal['month', 'year'],
    by_channel: bool,
    user_id: int,
    **filters,
) -> tuple[str, list[MentionCount]]:
    """Exhaustive full-text counts of `terms` per upload period of the user's transcripts.

    Each term matches as an exact phrase (see `fts_any_of`), and a block counts once
    however many it contains. Every transcribed video in scope counts toward its
    period's `videos`, mentions or not, so a count can be read against how much was
    uploaded then; `channel` is None unless `by_channel`. `filters` are
    `_build_media_conditions`'s. Returns how Postgres read the terms, e.g.
    `'carbon' <-> 'tax' | 'emiss'`, which is '' (and the rows empty) when every term
    is a stop word.
    """
    fts_query = fts_any_of(terms)
    subquery, scope_params = md_repo.build_media_scope_subquery(
        user_id=user_id,
        rating_user_id=user_id,
        include_owned=True,
        has_transcript=True,
        **filters,
    )
    channel = 's.channel' if by_channel else 'NULL::text'
    group_by = 's.period, s.channel' if by_channel else 's.period'
    order_by = (
        's.period NULLS LAST, blocks DESC, s.channel' if by_channel else 's.period NULLS LAST'
    )
    sql = f"""
        WITH scoped AS (
            SELECT
                md.id,
                coalesce(md.channel, :unknown_channel) AS channel,
                date_trunc(:unit, md.release_timestamp) AS period
            FROM media_details md
            WHERE md.id IN ({subquery})
        ),
        hits AS (
            SELECT tb.media_details_id AS media_id, count(*) AS blocks
            FROM transcript_blocks tb
            WHERE tb.text_search @@ to_tsquery('english', :fts_query)
                AND tb.media_details_id IN (SELECT id FROM scoped)
            GROUP BY tb.media_details_id
        )
        SELECT
            s.period,
            {channel} AS channel,
            count(*) AS videos,
            count(h.media_id) AS videos_with_mentions,
            coalesce(sum(h.blocks), 0) AS blocks
        FROM scoped s
        LEFT JOIN hits h ON h.media_id = s.id
        GROUP BY {group_by}
        ORDER BY {order_by}
    """  # noqa: S608 — only fixed fragments and the compiled scope are interpolated
    async with db.get_async_session() as session:
        matched = (
            await session.execute(
                text("SELECT to_tsquery('english', :fts_query)::text"), {'fts_query': fts_query}
            )
        ).scalar_one()
        if not matched:
            return '', []
        result = await session.execute(
            text(sql),
            {
                'fts_query': fts_query,
                'unit': interval,
                'unknown_channel': md_repo.UNKNOWN_CHANNEL,
                **scope_params,
            },
        )
        return matched, [MentionCount(*row) for row in result]


async def describe_fts_query(keywords: str) -> str:
    """How Postgres reads a keyword string, e.g. `'carbon' <-> 'tax' | 'emiss'`.

    An empty result means every term was a stop word, so the search matches nothing.
    """
    async with db.get_async_session() as session:
        result = await session.execute(
            text("SELECT to_tsquery('english', :fts_query)::text"),
            {'fts_query': _build_fts_query(keywords)},
        )
        return result.scalar_one()


def _row_to_dict(row) -> dict:
    return {
        'transcript_block_id': row.transcript_block_id,
        'text': row.text,
        'similarity': float(row.score),
        'fts_rank': float(row.fts_rank) if row.fts_rank is not None else None,
        'start_time': row.start_time,
        'end_time': row.end_time,
        # Every consumer of a search hit is a media surface: `duration` drives the
        # clip editor's range and zoom, `thumbnail_path` the lock-screen artwork,
        # `release_timestamp` the upload date MCP results cite.
        # All three query builders must project the same columns or this raises.
        'media_details': {
            'id': row.md_id,
            'url': row.url,
            'title': row.title,
            'channel': row.channel,
            'media_type': row.media_type,
            'status': row.status,
            'duration': row.duration,
            'thumbnail_path': row.thumbnail_path,
            'release_timestamp': row.release_timestamp,
        },
    }


@dataclass(frozen=True)
class _MediaScope:
    """The library filter, resolved into what the raw-SQL search queries splice in.

    `exact` says the scope is small enough to compute every distance in it, which is
    the only way to get full recall out of a filtered vector search (see
    `_resolve_scope`).
    """

    subquery: str
    params: dict
    exact: bool
    empty: bool


# Above this many media a scoped vector search keeps using the HNSW index. Counted in
# media rather than blocks because that count is one cheap query on the small table;
# at ~100 blocks each this is ~100k blocks of exact distance work, and past it the
# filter is no longer narrowing enough to beat the index.
_EXACT_SCOPE_MEDIA_LIMIT = 1000


async def _resolve_scope(**filters) -> _MediaScope | None:
    """Resolve the library filter, or None when nothing is being filtered.

    `include_owned` is set here rather than by the caller because it is never
    optional for transcript search: a soft delete removes every MediaAccess row for
    the media, the owner's included, so the access subquery alone would hide an
    owner's own kept transcripts from them.
    """
    scope = md_repo.build_media_scope_subquery(**filters, include_owned=True)
    if scope is None:
        return None
    subquery, params = scope
    count = await md_repo.count_scoped_media(**filters, include_owned=True)
    return _MediaScope(
        subquery=subquery,
        params=params,
        exact=count <= _EXACT_SCOPE_MEDIA_LIMIT,
        empty=count == 0,
    )


def _scope_clause(scope: _MediaScope | None) -> str:
    """The scope as a WHERE fragment, keyed on the block rather than the media row.

    `transcript_blocks.media_details_id` is NOT NULL with an FK, so this filters
    identically to `md.id` while applying before the media_details join.
    """
    if scope is None:
        return ''
    # S608: the interpolated fragment is a SQLAlchemy-compiled SELECT carrying only
    # named binds, never user text — every value travels in `scope.params`. Same for
    # the three query builders below, whose only interpolations are these clauses.
    return f'AND tb.media_details_id IN ({scope.subquery})'


_HNSW_EF_SEARCH_DEFAULT = 40
_HNSW_EF_SEARCH_MAX = 1000
# Bounds the extra walking for a scope that matches almost nothing in the graph.
_HNSW_MAX_SCAN_TUPLES = 50_000


def _hnsw_ef_search(depth: int) -> int:
    return max(_HNSW_EF_SEARCH_DEFAULT, min(_HNSW_EF_SEARCH_MAX, depth))


async def _use_iterative_index_scan(session: AsyncSession, depth: int) -> None:
    """Let an HNSW scan later in this transaction return `depth` rows.

    An HNSW scan yields at most `hnsw.ef_search` rows (40 by default) and the scope
    filters them only afterwards, so without this a deep or scoped search on the
    index path comes back short while looking successful. An iterative scan keeps
    walking the graph until the LIMIT is met, in strict distance order so pages of
    one ranking stay consistent. `set_config(..., true)` is transaction-local: the
    pooled connection is back on the defaults once the session commits.
    """
    await session.execute(
        text(
            "SELECT set_config('hnsw.iterative_scan', 'strict_order', true), "
            "set_config('hnsw.ef_search', :ef_search, true), "
            "set_config('hnsw.max_scan_tuples', :max_scan_tuples, true)"
        ),
        {
            'ef_search': str(_hnsw_ef_search(depth)),
            'max_scan_tuples': str(_HNSW_MAX_SCAN_TUPLES),
        },
    )


def _scoped_vector_cte(scope: _MediaScope, distance_param: str) -> str:
    """Every in-scope distance, materialized.

    AS MATERIALIZED is the whole point and must not be removed: it forbids the planner
    from flattening this into the outer ORDER BY, which would let it answer from the
    HNSW index and post-filter. pgvector's post-filter silently returns far fewer rows
    than the LIMIT asks for — an empty result, not an error — whenever the scope is
    narrow, which is exactly when someone has drilled into a group folder.
    """
    return f"""
        scoped_vec AS MATERIALIZED (
            SELECT te.transcript_block_id AS id, te.embedding <=> :{distance_param} AS dist
            FROM transcript_embeddings te
            JOIN transcript_blocks tb ON tb.id = te.transcript_block_id
            WHERE tb.media_details_id IN ({scope.subquery})
        )
    """  # noqa: S608


async def get_hybrid_search_results(
    model: OnnxEmbedder,
    search_query: str,
    standard_search: str | None = None,
    semantic_weight: float = 0.5,
    limit: int = 100,
    user_id: int | None = None,
    rating_user_id: int | None = None,
    tag_ids: list[int] | None = None,
    min_rating: int | None = None,
    channel: str | None = None,
    untagged: bool = False,
    date_field: str | None = None,
    date_year: int | None = None,
    date_month: int | None = None,
    channels: list[str] | None = None,
    released_from: datetime | None = None,
    released_to: datetime | None = None,
    fts_text: str | None = None,
    title_contains: str | None = None,
    media_ids: list[int] | None = None,
) -> list[dict]:
    """
    Search transcript blocks using hybrid keyword + vector search with Reciprocal Rank Fusion.

    Three modes based on semantic_weight:
    - 1.0: Pure semantic (pgvector cosine similarity)
    - 0.0: Pure keyword (native PostgreSQL FTS)
    - 0 < weight < 1: Hybrid RRF combining both rankings

    Every filter beyond the two search strings narrows the candidate media the same way
    the media list does, through `_build_media_conditions` — so `standard_search` here
    means what it means in the Downloads box, `&&` / `||` operators included.

    Args:
        user_id: Optional user ID to filter by access. None = no filter (admin view).
        channels / released_from / released_to: narrow the scope like the other filters
            (released_to exclusive); see `_build_media_conditions`.
        title_contains: like `standard_search` but matching only the title, never the channel.
        fts_text: the full-text side's query when it should differ from the embedded
            `search_query`, so exact names can boost a paraphrased semantic query. In pure
            keyword mode an empty `search_query` skips the embedding altogether.
        media_ids: only these media; an empty list matches nothing.

    The returned list is the cached object itself: callers must not mutate it.
    """
    cache_key = (
        search_query,
        standard_search,
        limit,
        semantic_weight,
        user_id,
        rating_user_id,
        tuple(tag_ids or ()),
        min_rating,
        channel,
        untagged,
        date_field,
        date_year,
        date_month,
        # Every parameter that changes the result belongs here, or a call differing
        # only in it is served another call's hits.
        tuple(channels) if channels is not None else None,
        released_from,
        released_to,
        fts_text,
        title_contains,
        tuple(sorted(media_ids)) if media_ids is not None else None,
    )
    if cache_key in semantic_cache:
        return semantic_cache[cache_key]

    scope = await _resolve_scope(
        user_id=user_id,
        search=standard_search,
        rating_user_id=rating_user_id,
        tag_ids=tag_ids,
        min_rating=min_rating,
        channel=channel,
        untagged=untagged,
        date_field=date_field,
        date_year=date_year,
        date_month=date_month,
        channels=channels,
        released_from=released_from,
        released_to=released_to,
        title_contains=title_contains,
        media_ids=media_ids,
    )
    # Nothing in scope: answer without paying for an embedding.
    if scope is not None and scope.empty:
        return []

    if semantic_weight == 1.0:
        results = await _semantic_search(model, search_query, limit, scope)
    elif semantic_weight == 0.0:
        results = await _keyword_search(model, search_query, limit, scope, fts_text)
    else:
        results = await _hybrid_rrf_search(
            model, search_query, semantic_weight, limit, scope, fts_text
        )

    # cachetools refuses a single value larger than the whole cache.
    with contextlib.suppress(ValueError):
        semantic_cache[cache_key] = results
    return results


async def _semantic_search(
    model: OnnxEmbedder,
    search_query: str,
    limit: int,
    scope: _MediaScope | None = None,
) -> list[dict]:
    """Pure vector cosine similarity search."""
    query_embedding = model.encode([search_query], normalize_embeddings=True)[0]
    query_str = _embedding_to_pgvector_str(query_embedding.tolist())
    exact = scope is not None and scope.exact

    if exact:
        sql = f"""
            WITH {_scoped_vector_cte(scope, 'query')}
            SELECT
                tb.id as transcript_block_id,
                tb.text,
                tb.start_time,
                tb.end_time,
                md.id as md_id,
                md.url,
                md.title,
                md.channel,
                md.media_type,
                md.status,
                md.duration,
                md.thumbnail_path,
                md.release_timestamp,
                1.0 - s.dist as score,
                NULL::float as fts_rank
            FROM scoped_vec s
            JOIN transcript_blocks tb ON tb.id = s.id
            JOIN media_details md ON md.id = tb.media_details_id
            ORDER BY s.dist ASC, tb.id
            LIMIT :limit
        """  # noqa: S608
    else:
        sql = f"""
            SELECT
                tb.id as transcript_block_id,
                tb.text,
                tb.start_time,
                tb.end_time,
                md.id as md_id,
                md.url,
                md.title,
                md.channel,
                md.media_type,
                md.status,
                md.duration,
                md.thumbnail_path,
                md.release_timestamp,
                1.0 - (te.embedding <=> :query) as score,
                NULL::float as fts_rank
            FROM transcript_embeddings te
            JOIN transcript_blocks tb ON tb.id = te.transcript_block_id
            JOIN media_details md ON md.id = tb.media_details_id
            WHERE 1=1 {_scope_clause(scope)}
            ORDER BY te.embedding <=> :query ASC
            LIMIT :limit
        """  # noqa: S608

    params: dict = {'query': query_str, 'limit': limit}
    if scope is not None:
        params.update(scope.params)

    async with db.get_async_session() as session:
        if not exact:
            await _use_iterative_index_scan(session, limit)
        result = await session.execute(text(sql), params)
        rows = result.fetchall()

    return [_row_to_dict(row) for row in rows]


async def _keyword_search(
    model: OnnxEmbedder,
    search_query: str,
    limit: int,
    scope: _MediaScope | None = None,
    fts_text: str | None = None,
) -> list[dict]:
    """Pure keyword search via native PostgreSQL FTS, ranked by ts_rank_cd.

    The display score is cosine similarity to `search_query`, or the FTS rank itself
    when there is no query to embed (MCP's keywords-only search).
    """
    fts_query = _build_fts_query(fts_text or search_query)
    params: dict = {'fts_query': fts_query, 'limit': limit}
    if search_query.strip():
        query_embedding = model.encode([search_query], normalize_embeddings=True)[0]
        params['embedding'] = _embedding_to_pgvector_str(query_embedding.tolist())
        score = '1.0 - (te.embedding <=> :embedding)'
        embedding_join = 'JOIN transcript_embeddings te ON te.transcript_block_id = tb.id'
    else:
        score = 'fr.fts_rank'
        embedding_join = ''

    sql = f"""
        WITH fts_ranked AS (
            SELECT
                tb.id,
                ts_rank_cd(tb.text_search, to_tsquery('english', :fts_query)) AS fts_rank
            FROM transcript_blocks tb
            WHERE tb.text_search @@ to_tsquery('english', :fts_query)
                {_scope_clause(scope)}
            ORDER BY fts_rank DESC, tb.id
            LIMIT :limit
        )
        SELECT
            tb.id as transcript_block_id,
            tb.text,
            tb.start_time,
            tb.end_time,
            md.id as md_id,
            md.url,
            md.title,
            md.channel,
            md.media_type,
            md.status,
            md.duration,
            md.thumbnail_path,
            md.release_timestamp,
            {score} as score,
            fr.fts_rank as fts_rank
        FROM fts_ranked fr
        JOIN transcript_blocks tb ON tb.id = fr.id
        JOIN media_details md ON md.id = tb.media_details_id
        {embedding_join}
        ORDER BY fr.fts_rank DESC, tb.id
    """  # noqa: S608

    if scope is not None:
        params.update(scope.params)

    async with db.get_async_session() as session:
        result = await session.execute(text(sql), params)
        rows = result.fetchall()

    return [_row_to_dict(row) for row in rows]


async def _hybrid_rrf_search(
    model: OnnxEmbedder,
    search_query: str,
    semantic_weight: float,
    limit: int,
    scope: _MediaScope | None = None,
    fts_text: str | None = None,
) -> list[dict]:
    """Hybrid search using RRF for ranking, cosine similarity for display score."""
    query_embedding = model.encode([search_query], normalize_embeddings=True)[0]
    query_str = _embedding_to_pgvector_str(query_embedding.tolist())
    fts_query = _build_fts_query(fts_text or search_query)

    keyword_weight = 1.0 - semantic_weight
    candidate_limit = limit * 3  # Fetch more candidates for better fusion
    exact = scope is not None and scope.exact

    if exact:
        vector_cte = f"""
            {_scoped_vector_cte(scope, 'embedding')},
            vector_search AS (
                SELECT id, ROW_NUMBER() OVER (ORDER BY dist, id) AS rank
                FROM scoped_vec
                ORDER BY dist ASC, id
                LIMIT :candidate_limit
            )
        """  # noqa: S608
    else:
        vector_cte = f"""
            vector_search AS (
                SELECT
                    te.transcript_block_id AS id,
                    ROW_NUMBER() OVER (ORDER BY te.embedding <=> :embedding) AS rank
                FROM transcript_embeddings te
                JOIN transcript_blocks tb ON tb.id = te.transcript_block_id
                WHERE 1=1 {_scope_clause(scope)}
                ORDER BY te.embedding <=> :embedding ASC
                LIMIT :candidate_limit
            )
        """  # noqa: S608

    sql = f"""
        WITH {vector_cte},
        keyword_search AS (
            SELECT
                tb.id,
                ts_rank_cd(tb.text_search, to_tsquery('english', :fts_query)) AS fts_rank,
                ROW_NUMBER() OVER (
                    ORDER BY ts_rank_cd(tb.text_search, to_tsquery('english', :fts_query)) DESC,
                        tb.id
                ) AS rank
            FROM transcript_blocks tb
            WHERE tb.text_search @@ to_tsquery('english', :fts_query)
                {_scope_clause(scope)}
            ORDER BY fts_rank DESC, tb.id
            LIMIT :candidate_limit
        )
        SELECT
            tb.id as transcript_block_id,
            tb.text,
            tb.start_time,
            tb.end_time,
            md.id as md_id,
            md.url,
            md.title,
            md.channel,
            md.media_type,
            md.status,
            md.duration,
            md.thumbnail_path,
            md.release_timestamp,
            1.0 - (te.embedding <=> :embedding) as score,
            k.fts_rank as fts_rank
        FROM transcript_blocks tb
        JOIN media_details md ON md.id = tb.media_details_id
        JOIN transcript_embeddings te ON te.transcript_block_id = tb.id
        LEFT JOIN vector_search v ON tb.id = v.id
        LEFT JOIN keyword_search k ON tb.id = k.id
        WHERE (v.id IS NOT NULL OR k.id IS NOT NULL)
            {_scope_clause(scope)}
        ORDER BY
            :semantic_weight * COALESCE(1.0 / (60 + v.rank), 0.0) +
            :keyword_weight * COALESCE(1.0 / (60 + k.rank), 0.0) DESC,
            tb.id
        LIMIT :limit
    """  # noqa: S608

    params: dict = {
        'embedding': query_str,
        'fts_query': fts_query,
        'semantic_weight': semantic_weight,
        'keyword_weight': keyword_weight,
        'candidate_limit': candidate_limit,
        'limit': limit,
    }
    if scope is not None:
        params.update(scope.params)

    async with db.get_async_session() as session:
        if not exact:
            await _use_iterative_index_scan(session, candidate_limit)
        result = await session.execute(text(sql), params)
        rows = result.fetchall()

    return [_row_to_dict(row) for row in rows]
