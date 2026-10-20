import contextlib
import glob
import os
import shutil

from config import settings
from logger import logger
from models import MediaType
from ytdlp.urls import get_url_hash, youtube_video_id

VIDEO_EXTENSIONS = ('mp4', 'mkv', 'webm', 'avi', 'mov', 'flv')
AUDIO_EXTENSIONS = ('m4a', 'mp3', 'opus', 'wav', 'flac', 'ogg', 'aac')

# Transcript-only items stage their captions or Whisper audio here, never in the library
# folders. Dot-prefixed so media servers scanning the audio path skip it.
TRANSCRIPT_STAGING_DIRNAME = '.transcript-only'
# Thumbnails of items that have a transcript but no media file (transcript-only, or
# deleted with the transcript kept). Kept apart from the staging, whose sweep deletes
# anything old in it.
THUMBNAILS_DIRNAME = '.thumbnails'


def transcript_staging_dir() -> str:
    return os.path.join(settings.storage.audio_path, TRANSCRIPT_STAGING_DIRNAME)


def transcript_staging_base(url: str) -> str:
    """Extensionless path that every staged file of a transcript-only item starts with.

    Derived from the URL alone so the download job and the transcript job (including one
    rebuilt by startup recovery, which has only the database row) agree on it.
    """
    return os.path.join(transcript_staging_dir(), get_url_hash(url))


def find_staged_audio(url: str) -> str | None:
    """The Whisper-fallback download staged for a transcript-only item, if any.

    'worstaudio/worst' can resolve to a video container, so both extension sets apply.
    """
    return find_existing_file(
        f'{transcript_staging_base(url)}.%(ext)s', extensions=AUDIO_EXTENSIONS + VIDEO_EXTENSIONS
    )


def delete_transcript_staging(url: str) -> int:
    """Delete every staged file of a transcript-only item; returns how many went."""
    deleted = 0
    for path in glob.glob(f'{glob.escape(transcript_staging_base(url))}.*'):
        try:
            os.remove(path)
            deleted += 1
        except OSError as e:
            logger.warning(f'Failed to delete staged transcript file {path}: {e}')
    return deleted


def find_existing_file(
    outtmpl: str, audio_only: bool = False, extensions: tuple[str, ...] | None = None
) -> str | None:
    """
    Check if a file already exists matching the outtmpl pattern.

    Args:
        outtmpl: The output template path with %(ext)s placeholder
        audio_only: If True, only check audio extensions; otherwise check video extensions
        extensions: Check exactly these extensions instead (overrides audio_only)

    Returns:
        The full file path if found, None otherwise
    """
    if '%(ext)s' not in outtmpl:
        if os.path.isfile(outtmpl):
            return outtmpl
        return None

    if extensions is None:
        extensions = AUDIO_EXTENSIONS if audio_only else VIDEO_EXTENSIONS
    base_path = outtmpl.replace('.%(ext)s', '')

    for ext in extensions:
        file_path = f'{base_path}.{ext}'
        if os.path.isfile(file_path):
            logger.info(f'Found existing file: {file_path}')
            return file_path

    return None


def download_thumbnail(thumbnail_url: str, media_file_path: str) -> str | None:
    """Download a thumbnail as a sidecar file next to the media file.

    Uses the same sidecar convention as .peaks.json.gz: {base_path}.thumb.jpg alongside
    the media file. Returns its path, or None on any failure (thumbnails are non-critical).
    """
    return save_thumbnail(thumbnail_url, os.path.splitext(media_file_path)[0] + '.thumb.jpg')


def save_thumbnail(image_url: str, dest: str) -> str | None:
    """Fetch an image to `dest`; returns `dest`, or None on any failure."""
    import httpx

    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(image_url)
            resp.raise_for_status()

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'wb') as f:
            f.write(resp.content)

        logger.info(f'Downloaded thumbnail to {dest}')
    except Exception as e:  # noqa: BLE001 — thumbnails are optional; failure must not fail the download
        logger.warning(f'Failed to download thumbnail from {image_url}: {e}')
        with contextlib.suppress(OSError):
            if os.path.isfile(dest):
                os.remove(dest)
        return None
    else:
        return dest


def thumbnail_source_url(url: str, info: dict | None) -> str | None:
    """Where a transcript-only item's thumbnail comes from.

    For YouTube, the 480x360 hqdefault: every video has one, it is a tenth the size of
    the maxres image yt-dlp prefers, and grid cards never show more than ~250px of it.
    It also needs no extract_info, which the reuse-what-was-staged path doesn't have.
    """
    video_id = youtube_video_id(url)
    if video_id:
        return f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg'
    return info.get('thumbnail') if info else None


def kept_thumbnail_path(url: str, media_type: MediaType) -> str:
    """Where the thumbnail of an item with a transcript but no media file is kept.

    Keyed like uq_media_details_url_type, so a deleted video and a transcript-only item
    of the same URL never share a file.
    """
    return os.path.join(
        kept_thumbnails_dir(), f'{get_url_hash(url)}.{MediaType(media_type).value.lower()}.jpg'
    )


def kept_thumbnails_dir() -> str:
    return os.path.join(settings.storage.audio_path, THUMBNAILS_DIRNAME)


def staged_thumbnail_path(url: str) -> str:
    """A transcript-only item's thumbnail until its transcript lands.

    Staged rather than kept so every way the chain ends without a transcript (failure,
    cancel, the orphan sweep) deletes it along with the captions or audio.
    """
    return transcript_staging_base(url) + '.thumb.jpg'


def move_thumbnail(src: str, dest: str) -> str | None:
    """Move a thumbnail to its kept path; returns `dest`, or None if there was nothing to move.

    shutil.move rather than os.rename: the video folder can be a different mount from
    the audio folder the kept thumbnails live under.
    """
    if src == dest:
        return dest
    if not os.path.isfile(src):
        return None
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(src, dest)
    except OSError as e:
        logger.warning(f'Failed to keep thumbnail {src}: {e}')
        return None
    return dest


def delete_thumbnail(path: str | None) -> None:
    if path:
        with contextlib.suppress(OSError):
            os.remove(path)
