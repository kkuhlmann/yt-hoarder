"""
Subtitle file detection and parsing for yt-dlp downloaded subtitles.

Supports YouTube JSON3 format (primary) and WebVTT (fallback).
Returns segments as (start_seconds, end_seconds, text) tuples,
matching the same format as whisper output for seamless integration.
"""

import json
import os
import re
from urllib.parse import parse_qs, urlparse

from yt_dlp.utils import DownloadError

from logger import logger

# yt-dlp raises this from _write_subtitles when a listed caption track fails to download
# (a 429 or 403 from the caption endpoint), before any media bytes move. A video with no
# track for the requested language raises nothing; it only logs.
SUBTITLE_DOWNLOAD_ERROR = 'Unable to download video subtitles'


def is_subtitle_download_error(e: Exception) -> bool:
    return isinstance(e, DownloadError) and SUBTITLE_DOWNLOAD_ERROR in str(e)


def prefer_untranslated_captions(info: dict) -> None:
    """Narrow `info`'s English auto-captions to YouTube's own track, when it has one.

    yt-dlp files a machine translation (`tlang=en`) of every auto-caption track under `en`
    and picks the last, and auto-dubbing gives each dubbed audio track a track of its own.
    So a dubbed English video's captions sit among translations of its dubs back into
    English, which YouTube 429s. A video with no untranslated `en` track is left alone.

    Call it between extract_info and process_ie_result, which re-selects from
    info['automatic_captions'].
    """
    captions = info.get('automatic_captions') or {}
    untranslated = [
        track
        for track in captions.get('en') or []
        if 'tlang' not in parse_qs(urlparse(track.get('url') or '').query)
    ]
    if untranslated:
        captions['en'] = untranslated


def find_subtitle_file(media_file_path: str) -> str | None:
    """Find a subtitle file adjacent to the media file.

    Checks for .en.json3 first (YouTube's native format with ms-precision timing),
    then falls back to .en.vtt (WebVTT).

    Args:
        media_file_path: Path to the downloaded media file.

    Returns:
        Path to subtitle file if found, None otherwise.
    """
    base_path = os.path.splitext(media_file_path)[0]
    for ext in ('.en.json3', '.en.vtt'):
        sub_path = base_path + ext
        if os.path.isfile(sub_path):
            return sub_path
    return None


def parse_json3_subtitles(file_path: str) -> list[tuple[float, float, str]]:
    """Parse YouTube JSON3 subtitle format.

    JSON3 format: {"events": [{"tStartMs": int, "dDurationMs": int, "segs": [{"utf8": str}]}]}
    Events without "segs" are metadata/styling and are skipped.

    Args:
        file_path: Path to the .json3 subtitle file.

    Returns:
        List of (start_seconds, end_seconds, text) tuples.
    """
    with open(file_path, encoding='utf-8') as f:
        data = json.load(f)

    segments = []
    for event in data.get('events', []):
        if 'segs' not in event:
            continue

        text = ''.join(seg.get('utf8', '') for seg in event['segs']).strip()
        if not text:
            continue

        start_ms = event.get('tStartMs', 0)
        duration_ms = event.get('dDurationMs', 0)
        start = start_ms / 1000.0
        end = (start_ms + duration_ms) / 1000.0

        segments.append((start, end, text))

    return segments


def parse_vtt_subtitles(file_path: str) -> list[tuple[float, float, str]]:
    """Parse WebVTT subtitle format.

    Handles standard VTT timestamps (HH:MM:SS.mmm --> HH:MM:SS.mmm)
    and strips VTT formatting tags (<c>, </c>, <b>, etc.).

    Args:
        file_path: Path to the .vtt subtitle file.

    Returns:
        List of (start_seconds, end_seconds, text) tuples.
    """
    with open(file_path, encoding='utf-8') as f:
        content = f.read()

    # Match timestamp lines: 00:00:01.000 --> 00:00:05.000
    timestamp_pattern = re.compile(
        r'(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})'
    )
    tag_pattern = re.compile(r'<[^>]+>')

    segments = []
    lines = content.split('\n')
    i = 0

    while i < len(lines):
        match = timestamp_pattern.match(lines[i].strip())
        if match:
            h1, m1, s1, ms1, h2, m2, s2, ms2 = match.groups()
            start = int(h1) * 3600 + int(m1) * 60 + int(s1) + int(ms1) / 1000.0
            end = int(h2) * 3600 + int(m2) * 60 + int(s2) + int(ms2) / 1000.0

            # Collect text lines until blank line or next timestamp
            text_lines = []
            i += 1
            while (
                i < len(lines)
                and lines[i].strip()
                and not timestamp_pattern.match(lines[i].strip())
            ):
                line = tag_pattern.sub('', lines[i].strip())
                if line:
                    text_lines.append(line)
                i += 1

            text = ' '.join(text_lines).strip()
            if text:
                segments.append((start, end, text))
        else:
            i += 1

    return segments


def parse_subtitle_file(file_path: str) -> list[tuple[float, float, str]]:
    """Parse a subtitle file, dispatching to the correct parser based on extension.

    On any error, returns an empty list and logs a warning so the caller
    can gracefully fall back to whisper transcription.

    Args:
        file_path: Path to the subtitle file (.json3 or .vtt).

    Returns:
        List of (start_seconds, end_seconds, text) tuples, or [] on failure.
    """
    try:
        if file_path.endswith('.json3'):
            return parse_json3_subtitles(file_path)
        if file_path.endswith('.vtt'):
            return parse_vtt_subtitles(file_path)
    except Exception as e:  # noqa: BLE001 — unparseable subtitles degrade to no transcript, not an error
        logger.warning(f'Failed to parse subtitle file {file_path}: {e}')
        return []
    else:
        logger.warning(f'Unsupported subtitle format: {file_path}')
        return []
