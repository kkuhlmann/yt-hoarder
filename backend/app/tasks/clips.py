import contextlib
import os
import subprocess
import time

from yt_dlp import YoutubeDL

from config import settings
from logger import logger
from models import MediaType
from orchestrator import JobCancelled, JobContext
from repositories import clips as clips_repo
from repositories import media_details as md_repo
from tasks.ffmpeg import run_ffmpeg_cancellable
from ytdlp.cookies import cookie_session
from ytdlp.info import is_video_ready_for_download
from ytdlp.options import create_source_clip_options

FFMPEG_CLIP_TIMEOUT_SECONDS = 600

# Video: re-encode with H.264/AAC for frame-accurate cuts
VIDEO_CLIP_CODEC_ARGS = ['-c:v', 'libx264', '-c:a', 'aac', '-preset', 'fast', '-crf', '23']
AUDIO_CLIP_CODEC_ARGS = ['-acodec', 'libmp3lame', '-ab', '192k']

# One ffmpeg input: a file path or stream URL, and the options that go before its `-i`.
ClipInput = tuple[str, list[str]]


def _build_ffmpeg_clip_command(
    inputs: list[ClipInput],
    output_path: str,
    start_time: float,
    duration: float,
    is_video: bool,
) -> list[str]:
    """Build FFmpeg command for creating a clip.

    Args:
        inputs: The sources to cut, each seeked to start_time. Two inputs are a separate
            video and audio stream, mapped into one output.
        output_path: Path for output clip file
        start_time: Start time in seconds
        duration: Duration of clip in seconds
        is_video: True for video clips, False for audio-only

    Returns:
        List of command arguments for subprocess
    """
    cmd = ['ffmpeg', '-y']
    for source, input_args in inputs:
        cmd += [*input_args, '-ss', str(start_time), '-i', source]
    cmd += ['-t', str(duration)]
    if len(inputs) > 1:
        cmd += ['-map', '0:v:0', '-map', '1:a:0']
    codec_args = VIDEO_CLIP_CODEC_ARGS if is_video else AUDIO_CLIP_CODEC_ARGS
    return [*cmd, *codec_args, output_path]


def _source_clip_inputs(info: dict, cookiejar) -> list[ClipInput]:
    """ffmpeg inputs for the streams yt-dlp selected, carrying what yt-dlp would send.

    Mirrors yt-dlp's own ffmpeg downloader (FFmpegFD): each stream URL gets its format's
    HTTP headers and any cookies the session holds for it.
    """
    inputs = []
    for fmt in info.get('requested_formats') or [info]:
        url = fmt['url']
        input_args = []
        cookies = cookiejar.get_cookies_for_url(url)
        if cookies:
            input_args += [
                '-cookies',
                ''.join(
                    f'{c.name}={c.value}; path={c.path}; domain={c.domain};\r\n' for c in cookies
                ),
            ]
        headers = fmt.get('http_headers') or info.get('http_headers')
        if headers:
            input_args += ['-headers', ''.join(f'{k}: {v}\r\n' for k, v in headers.items())]
        inputs.append((url, input_args))
    return inputs


def _wait_until_streams_served(ctx: JobContext, info: dict) -> None:
    """Sleep out the wait YouTube imposes before it serves a session's streams.

    YouTube holds the streams back for the length of the pre-roll ad it would have shown
    and 403s any fetch before then; yt-dlp records the moment as `available_at` and its
    own downloader sleeps until it, which ffmpeg reading the URLs directly must copy.
    """
    formats = info.get('requested_formats') or [info]
    wait = max((f.get('available_at') or 0 for f in formats), default=0) - time.time()
    if wait <= 0:
        return
    logger.info(f'Waiting {wait:.0f}s for YouTube to serve the streams')
    if ctx.cancel_event.wait(wait):
        msg = 'Clip cancelled'
        raise JobCancelled(msg)


def _clip_output_path(clip_id: int, is_video: bool) -> str:
    base_path = settings.storage.video_path if is_video else settings.storage.audio_path
    clips_dir = os.path.join(base_path, 'clips')
    os.makedirs(clips_dir, exist_ok=True)
    return os.path.join(clips_dir, f'clip_{clip_id}{".mp4" if is_video else ".mp3"}')


def _remove_partial(output_path: str) -> None:
    """A partial clip file is never recorded on the row, so no hook would find it."""
    with contextlib.suppress(OSError):
        os.remove(output_path)


def _encode_clip(ctx: JobContext, clip_data: dict, inputs: list[ClipInput]) -> dict:
    """Cut `inputs` into the clip's file and record it on the clip row.

    The command is not logged: for a source clip it carries signed stream URLs and cookies.
    """
    clip_id = clip_data['clip_id']
    start_time = clip_data['start_time']
    duration = clip_data['end_time'] - start_time
    is_video = clip_data['media_type'] == MediaType.VIDEO.value
    output_path = _clip_output_path(clip_id, is_video)

    cmd = _build_ffmpeg_clip_command(inputs, output_path, start_time, duration, is_video)
    logger.info(f'Running FFmpeg for clip {clip_id} from {len(inputs)} input(s)')
    try:
        result = run_ffmpeg_cancellable(
            cmd, FFMPEG_CLIP_TIMEOUT_SECONDS, cancel_event=ctx.cancel_event
        )
    except subprocess.TimeoutExpired as e:
        _remove_partial(output_path)
        msg = 'FFmpeg timed out after 10 minutes'
        raise RuntimeError(msg) from e
    except BaseException:
        _remove_partial(output_path)
        raise

    if result.returncode != 0:
        _remove_partial(output_path)
        logger.error(f'FFmpeg stderr: {result.stderr}')
        msg = f'FFmpeg failed with return code {result.returncode}'
        raise RuntimeError(msg)

    if not os.path.exists(output_path):
        msg = f'FFmpeg did not create output file: {output_path}'
        raise RuntimeError(msg)

    clips_repo.sync_update_clip(clip_id, {'file_path': output_path, 'duration': duration})
    logger.info(f'Successfully created clip {clip_id} at {output_path}')
    return {'clip_id': clip_id, 'file_path': output_path}


def run_clip_job(ctx: JobContext, clip_data: dict) -> dict:
    """Create a clip from a media file using FFmpeg.

    Args:
        clip_data: dict containing:
            - clip_id: int - The clip record ID
            - media_details_id: int - Source media ID
            - start_time: float - Start time in seconds
            - end_time: float - End time in seconds
            - media_type: str - 'AUDIO' or 'VIDEO'

    Returns:
        dict with clip_id and file_path
    """
    media_details_id = clip_data['media_details_id']
    logger.info(
        f'Creating clip {clip_data["clip_id"]} from media {media_details_id}: '
        f'{clip_data["start_time"]}s - {clip_data["end_time"]}s'
    )

    source_media = md_repo.sync_get_media_details_by_id(media_details_id)
    if not source_media or not source_media.file_path:
        msg = f'Source media {media_details_id} not found or has no file'
        raise ValueError(msg)

    return _encode_clip(ctx, clip_data, [(source_media.file_path, [])])


def run_source_clip_job(ctx: JobContext, clip_data: dict) -> dict:
    """Create a clip of an item with no file by cutting the section from its source.

    yt-dlp only resolves the streams; ffmpeg fetches and cuts just the section, so the
    encode stays cancellable and the output matches a clip cut from a local file.

    Args:
        clip_data: as for run_clip_job, plus source_url: str - the item's YouTube URL.
            media_type is the clip's output type, chosen by the user.
    """
    url = clip_data['source_url']
    logger.info(
        f'Creating clip {clip_data["clip_id"]} from {url}: '
        f'{clip_data["start_time"]}s - {clip_data["end_time"]}s'
    )

    audio_only = clip_data['media_type'] == MediaType.AUDIO.value
    with cookie_session(is_retry=ctx.attempt > 0) as cookie_file:
        options = create_source_clip_options(url, audio_only=audio_only, cookie_file=cookie_file)
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            is_ready, reason = is_video_ready_for_download(info)
            if not is_ready:
                msg = f'Source is not available to clip yet: {reason}'
                raise RuntimeError(msg)
            inputs = _source_clip_inputs(info, ydl.cookiejar)

    _wait_until_streams_served(ctx, info)
    return _encode_clip(ctx, clip_data, inputs)
