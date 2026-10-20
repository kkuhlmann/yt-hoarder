import glob
import os
import shutil
import tempfile
import time
from pathlib import Path

from config import settings
from logger import logger
from ytdlp.files import kept_thumbnails_dir, transcript_staging_dir
from ytdlp.urls import get_url_hash

SIDECAR_EXTENSIONS = (
    '.en.json3',
    '.en.vtt',
    '.whisper.json.gz',
    '.peaks.json.gz',
    '.thumb.jpg',
    '.sprites.jpg',
    '.sprites.json',
)


def delete_file(file_path: str, cleanup_sidecars: bool = False):
    """Delete a file and optionally its sidecar files (subtitles, thumbnails, etc.)."""
    if not file_path:
        return
    try:
        os.remove(file_path)
    except OSError as e:
        # A failed unlink is an expected operational condition (already gone,
        # permissions); the traceback adds noise, not information.
        logger.error(f'Error deleting file {file_path}: {e}')  # noqa: TRY400

    if cleanup_sidecars:
        base_path = os.path.splitext(file_path)[0]
        for ext in SIDECAR_EXTENSIONS:
            sub_path = base_path + ext
            try:
                if os.path.isfile(sub_path):
                    os.remove(sub_path)
            except OSError:
                pass


def cleanup_old_temp_files(age_hours: int = 24):
    """
    Clean up temporary files created during transcript generation.

    Removes:
    - /tmp/transcript_chunks_* directories older than age_hours
    - *_audio_transcript.mp3 files in media directories older than age_hours
    """
    current_time = time.time()
    age_seconds = age_hours * 3600
    deleted_count = 0

    # Must resolve the same way as the tempfile.mkdtemp() call in
    # services/transcript.py that created these directories.
    tmp_dir = Path(tempfile.gettempdir())
    if tmp_dir.exists():
        for item in tmp_dir.glob('transcript_chunks_*'):
            if not item.is_dir():
                continue

            try:
                dir_age = current_time - item.stat().st_mtime
                if dir_age > age_seconds:
                    shutil.rmtree(item)
                    logger.info(f'Deleted old temp directory: {item} (age: {dir_age / 3600:.1f}h)')
                    deleted_count += 1
            except Exception as e:  # noqa: BLE001 — best-effort sweep; one undeletable path must not stop it
                logger.warning(f'Failed to delete temp directory {item}: {e}')

    audio_path = settings.storage.audio_path
    video_path = settings.storage.video_path

    media_paths = [audio_path, video_path]

    for media_path in media_paths:
        if not media_path or not os.path.exists(media_path):
            continue

        media_dir = Path(media_path)
        for audio_file in media_dir.rglob('*_audio_transcript.mp3'):
            try:
                file_age = current_time - audio_file.stat().st_mtime
                if file_age > age_seconds:
                    audio_file.unlink()
                    logger.info(
                        f'Deleted old audio transcript file: {audio_file} '
                        f'(age: {file_age / 3600:.1f}h)'
                    )
                    deleted_count += 1
            except Exception as e:  # noqa: BLE001 — best-effort sweep; one undeletable path must not stop it
                logger.warning(f'Failed to delete audio transcript file {audio_file}: {e}')

    logger.info(
        f'Cleanup completed: removed {deleted_count} temporary files/directories '
        f'older than {age_hours} hours'
    )

    return deleted_count


def cleanup_orphaned_transcript_staging(age_hours: int, in_flight_hashes: set[str]) -> int:
    """Delete staged transcript-only files nothing is waiting for any more.

    TranscriptHooks removes them when a transcript job ends, so these are the leftovers
    of a crash between staging and that hook. Age alone isn't enough: behind a Whisper
    backlog a staged file can legitimately wait in the ml lane for longer than a day, so a
    file whose url hash (its name up to the first dot) belongs to an item still in flight
    is kept regardless.
    """
    staging_dir = Path(transcript_staging_dir())
    if not staging_dir.is_dir():
        return 0

    cutoff = time.time() - age_hours * 3600
    deleted = 0
    for item in staging_dir.iterdir():
        if not item.is_file() or item.name.split('.', 1)[0] in in_flight_hashes:
            continue
        try:
            if item.stat().st_mtime < cutoff:
                item.unlink()
                logger.info(f'Deleted orphaned transcript staging file: {item}')
                deleted += 1
        except OSError as e:
            logger.warning(f'Failed to delete transcript staging file {item}: {e}')
    return deleted


def cleanup_orphaned_thumbnails(age_hours: int, referenced: set[str]) -> int:
    """Delete kept thumbnails that no media row points at any more.

    The delete paths remove a thumbnail with its row's last media file or transcript, so
    these are leftovers of a crash between moving a file and writing its row, or of a row
    deleted outright. The age guard keeps the sweep off a thumbnail being kept right now,
    whose row isn't written yet.
    """
    thumbnails_dir = Path(kept_thumbnails_dir())
    if not thumbnails_dir.is_dir():
        return 0

    cutoff = time.time() - age_hours * 3600
    deleted = 0
    for item in thumbnails_dir.iterdir():
        if not item.is_file() or str(item) in referenced:
            continue
        try:
            if item.stat().st_mtime < cutoff:
                item.unlink()
                logger.info(f'Deleted orphaned thumbnail: {item}')
                deleted += 1
        except OSError as e:
            logger.warning(f'Failed to delete thumbnail {item}: {e}')
    return deleted


def cleanup_incomplete_downloads(age_hours: int = 1):
    """
    Clean up incomplete download files (partial downloads).

    Removes:
    - *.part files (yt-dlp partial downloads)
    - *.ytdl files (yt-dlp metadata files)
    - *.part-Frag* files (fragmented download parts)

    Args:
        age_hours: Age threshold in hours (default 1 - shorter for incomplete files)
    """
    current_time = time.time()
    age_seconds = age_hours * 3600
    deleted_count = 0

    media_paths = [settings.storage.audio_path, settings.storage.video_path]

    patterns = ['*.part', '*.ytdl', '*.part-Frag*']

    for media_path in media_paths:
        if not Path(media_path).exists():
            continue

        media_dir = Path(media_path)
        for pattern in patterns:
            for partial_file in media_dir.rglob(pattern):
                try:
                    file_age = current_time - partial_file.stat().st_mtime
                    if file_age > age_seconds:
                        file_size = partial_file.stat().st_size / (1024 * 1024)  # MB
                        partial_file.unlink()
                        logger.info(
                            f'Deleted incomplete download: {partial_file.name} '
                            f'({file_size:.2f} MB, age: {file_age / 3600:.1f}h)'
                        )
                        deleted_count += 1
                except Exception as e:  # noqa: BLE001 — best-effort sweep; one undeletable path must not stop it
                    logger.warning(f'Failed to delete partial file {partial_file}: {e}')

    if deleted_count > 0:
        logger.info(f'Cleanup completed: removed {deleted_count} incomplete download files')

    return deleted_count


def cleanup_task_files(task_title: str | None = None, task_url: str | None = None):
    """
    Delete the partial download files (.part/.ytdl/.part-Frag*) a cancelled or failed
    task left behind.

    Args:
        task_title: Title of the video, used only when no URL is available
        task_url: URL of the video; preferred, since its hash appears in the filename

    Returns:
        Number of files deleted
    """
    if task_url:
        # clean_outtmpl names every download '{title} [{url_hash}].{ext}', so the hash
        # matches this task's own partials and nothing else. Titles are user-influenced
        # and a glob on one can reach other tasks' files.
        match_token = f' [{get_url_hash(task_url)}]'
    elif task_title:
        match_token = task_title
    else:
        logger.warning('Cannot cleanup task files: no url or title provided')
        return 0

    deleted_count = 0
    media_paths = [settings.storage.audio_path, settings.storage.video_path]

    # fnmatch has no escape character, so hand-rolled '\[' matches nothing and an
    # unescaped '*' in a title would sweep every partial in the library.
    safe_token = glob.escape(match_token)

    patterns = [
        f'*{safe_token}*.part',
        f'*{safe_token}*.ytdl',
        f'*{safe_token}*.part-Frag*',
    ]

    for media_path in media_paths:
        if not Path(media_path).exists():
            continue

        media_dir = Path(media_path)
        for pattern in patterns:
            for file in media_dir.rglob(pattern):
                try:
                    file_size = file.stat().st_size / (1024 * 1024)  # MB
                    file.unlink()
                    logger.info(
                        f'Deleted partial file for cancelled task: {file.name} ({file_size:.2f} MB)'
                    )
                    deleted_count += 1
                except Exception as e:  # noqa: BLE001 — best-effort sweep; one undeletable path must not stop it
                    logger.warning(f'Failed to delete file {file}: {e}')

    if deleted_count > 0:
        logger.info(f'Cleaned up {deleted_count} file(s) for task: {task_title or task_url}')

    return deleted_count
