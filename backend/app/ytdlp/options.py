import contextlib
import os
import random
from functools import lru_cache
from string import printable

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor import PostProcessor

from config import settings
from logger import logger
from models import DownloadJob, MediaType
from repositories import media_details as md_repo
from repositories import settings as settings_repo
from schemas import DownloadJobDTO
from ytdlp.subtitles import SUBTITLE_DOWNLOAD_ERROR
from ytdlp.urls import is_youtube_url

AUDIO_FORMATS = ('m4a', 'mp3', 'opus', 'wav', 'flac')

# Maps DownloadQuality enum values to yt-dlp quality strings
DOWNLOAD_QUALITY_MAP = {
    'BEST': 'best_ios',
    '1440P': '1440',
    '1080P': '1080',
    '720P': '720',
    '480P': '480',
    '360P': '360',
}


def download_quality_to_ytdlp(quality) -> str:
    val = quality.value if hasattr(quality, 'value') else quality
    return DOWNLOAD_QUALITY_MAP.get(val, 'best_ios')


# Maps AudioQuality enum values to a max audio bitrate cap in kbps.
# BEST / unknown -> None (no cap, pick the best available stream).
AUDIO_QUALITY_MAP = {
    'BEST': None,
    '128K': 128,
    '96K': 96,
    '64K': 64,
    '48K': 48,
}


def audio_quality_to_abr_cap(quality) -> int | None:
    """Convert AudioQuality enum to a max abr cap in kbps (None = no cap / best)."""
    val = quality.value if hasattr(quality, 'value') else quality
    return AUDIO_QUALITY_MAP.get(val)


def build_audio_selection(abr_cap: int | None) -> tuple[str, int | None]:
    """Pick the audio format selector + FFmpegExtractAudio target bitrate for a tier.

    YouTube only serves m4a/AAC at ~48k and ~128k; the finer bitrates exist only as
    opus, and the app must output m4a (iOS/Safari). So the higher tiers copy a native
    AAC stream losslessly, and the sub-128k tiers re-encode DOWN to the exact target.

    Returns (yt-dlp format selector, FFmpegExtractAudio preferredquality in kbps, or
    None for "copy / no target"). abr_cap=None is "Best".
    """
    if abr_cap is None:
        # Best: highest-quality source, copied to m4a (or transcoded if opus-only).
        return 'bestaudio/best', None
    if abr_cap >= 128:
        # Copy the native ~128k AAC stream losslessly. YouTube reports it as ~129.5k,
        # so add headroom or an exact abr<=128 would wrongly exclude it.
        eff = round(abr_cap * 1.15)
        return f'bestaudio[ext=m4a][abr<={eff}]/bestaudio/best', None
    # Lower tiers: re-encode DOWN to the exact target. Prefer an opus source so the
    # extract-audio step actually re-encodes (an AAC source is copied, which would
    # ignore the target bitrate).
    return 'bestaudio[acodec=opus]/bestaudio/best', abr_cap


BGUTIL_POT_PATH = '/usr/local/bin/bgutil-pot'


def _get_pot_extractor_args() -> dict:
    """Get PO token provider extractor-args if the binary is available."""
    if not os.path.isfile(BGUTIL_POT_PATH):
        return {}
    return {
        # yt-dlp keys this off the provider's class name (BgUtilCliPTP -> BgUtilCli),
        # not its PROVIDER_NAME, and silently ignores a key it does not recognise.
        'youtubepot-bgutilcli': {'cli_path': [BGUTIL_POT_PATH]},
        # bgutil's HTTP provider reads this key to decide whether a refused connection
        # to its :4416 server is expected. Without it, every extraction warns.
        'youtubepot-bgutilscript': {'script_path': [BGUTIL_POT_PATH]},
    }


@lru_cache(maxsize=1)
def get_available_impersonate_targets() -> tuple:
    """
    Get all available impersonation targets from yt-dlp (cached).

    Returns:
        Tuple of ImpersonateTarget objects.
        Empty tuple if no targets are available.
    """
    try:
        with YoutubeDL({'quiet': True}) as ydl:
            # SLF001: yt-dlp exposes no public accessor for this. Real coupling —
            # if a yt-dlp bump renames it, impersonation silently degrades to none
            # (the except below returns an empty tuple).
            targets = ydl._get_available_impersonate_targets()  # noqa: SLF001
            if not targets:
                logger.debug('No impersonation targets available')
                return ()

            # Extract just the ImpersonateTarget objects (first element of each tuple)
            target_objects = tuple(target for target, backend in targets)
            logger.info(
                f'Available impersonation targets ({len(target_objects)}): {[str(t) for t in target_objects[:5]]}...'
            )
            return target_objects
    except Exception as e:  # noqa: BLE001 — impersonation is optional; fall back to none
        logger.warning(f'Failed to get impersonation targets: {e}')
        return ()


def get_random_impersonate_target():
    """
    Get a random impersonation target from the cached list.

    Returns:
        A random ImpersonateTarget object, or None if no targets are available.
    """
    targets = get_available_impersonate_targets()
    if not targets:
        return None

    selected = random.choice(targets)  # noqa: S311 — picks a browser fingerprint, not a secret
    logger.debug(f'Selected impersonation target: {selected}')
    return selected


@lru_cache(maxsize=1)
def get_stable_impersonate_target():
    """One fixed impersonation target, chosen deterministically from what curl-cffi offers.

    Picked by sort order rather than hardcoded so the choice survives curl-cffi dropping
    a target; stable across restarts so an account's fingerprint does not move between jobs.
    """
    targets = get_available_impersonate_targets()
    if not targets:
        return None
    return min(targets, key=str)


class YtPostProcessor(PostProcessor):
    def __init__(self, media_details_id: int):
        super().__init__()
        self.media_details_id = media_details_id

    def run(self, info):
        file_path = info['filepath']
        updates = {'file_path': file_path}
        with contextlib.suppress(OSError):
            updates['file_size_bytes'] = os.path.getsize(file_path)
        # Update by primary key only. Flushing a detached ORM instance here would
        # raise StaleDataError if a concurrent chain replaced the row mid-download,
        # and an url-keyed upsert would clobber the live row's status.
        updated = md_repo.sync_update_by_id(self.media_details_id, **updates)
        if updated is None:
            logger.warning(
                f'MediaDetails id={self.media_details_id} missing during post-processing; '
                f'file saved to {file_path} but not recorded'
            )
        return [], info


class YtDlLogger:
    def debug(self, msg):
        logger.debug(msg)

    def info(self, msg):
        logger.info(msg)

    def warning(self, msg):
        logger.warning(msg)

    def error(self, msg):
        # The download recovers by re-running without subtitles (tasks.downloads), so a
        # failed caption fetch is not an error of the job.
        if SUBTITLE_DOWNLOAD_ERROR in msg:
            logger.warning(msg)
            return
        logger.error(msg)


def get_fallback_format(audio_only: bool) -> str:
    """
    Generic format string that works with any video.

    Used when the specific format requested is not available. This returns
    a permissive format selector that lets yt-dlp choose the best available
    format, which is then converted via FFmpeg post-processing.

    Args:
        audio_only: If True, return audio-only format selector

    Returns:
        Format string for yt-dlp
    """
    if audio_only:
        return 'bestaudio/best'
    return 'bestvideo+bestaudio/best'


def get_format(fmt: str, quality: str) -> str:
    """
    Pulled from the great metube project: https://github.com/alexta69/metube
    Returns format for download

    Args:
      fmt (str): format selected
      quality (str): quality selected

    Raises:
      ValueError: unknown quality, unknown format

    Returns:
      dl_format: Formatted download string
    """
    fmt = fmt or 'any'

    if fmt.startswith('custom:'):
        return fmt[7:]

    if fmt == 'thumbnail':  # keep for when we add thumbnail downloads
        # Quality is irrelevant in this case since we skip the download
        return 'bestaudio/best'

    if fmt in AUDIO_FORMATS:
        # Audio quality needs to be set post-download, set in opts
        return f'bestaudio[ext={fmt}]/bestaudio/best'

    if fmt in ('mp4', 'any'):
        if quality == 'audio':
            return 'bestaudio/best'

        if quality == 'best_ios':
            # iOS requires h264/h265 video codec and aac audio in MP4 container.
            # Resolution sorting handled by format_sort in create_ydl_options().
            return (
                "bestvideo[vcodec~='^((he|a)vc|h26[45])']+bestaudio[acodec=aac]/"
                "bestvideo[vcodec~='^((he|a)vc|h26[45])']+bestaudio[ext=m4a]/"
                'bestvideo[ext=mp4]+bestaudio[ext=m4a]/'
                'best[ext=mp4]'
            )

        if quality not in ('best', 'worst'):
            # Resolution-limited: same iOS-compatible codec chain but with height cap
            res = quality
            return (
                f"bestvideo[height<={res}][vcodec~='^((he|a)vc|h26[45])']+bestaudio[acodec=aac]/"
                f"bestvideo[height<={res}][vcodec~='^((he|a)vc|h26[45])']+bestaudio[ext=m4a]/"
                f'bestvideo[height<={res}][ext=mp4]+bestaudio[ext=m4a]/'
                f'best[height<={res}][ext=mp4]'
            )

        # Fallback for 'best' / 'worst'
        vfmt, afmt = ('[ext=mp4]', '[ext=m4a]') if fmt == 'mp4' else ('', '')
        return f'bestvideo{vfmt}+bestaudio{afmt}/best{vfmt}'

    msg = f'Unknown format {fmt}'
    raise ValueError(msg)


def _throttling_options(app_settings) -> dict:
    """yt-dlp throttling keys for the current app settings; 0 means off for both.

    Kept out of the metadata builder in ytdlp/info.py on purpose: `ratelimit` caps a media
    transfer, and metadata extraction has none to cap.
    """
    options = {}
    if app_settings.download_rate_limit_kbps > 0:
        options['ratelimit'] = app_settings.download_rate_limit_kbps * 1024
    if app_settings.request_sleep_seconds > 0:
        options['sleep_interval_requests'] = app_settings.request_sleep_seconds
    return options


def _media_format_options(
    download_job: DownloadJob | DownloadJobDTO,
    fmt: str,
    quality: str,
    audio_abr_cap: int | None,
    use_fallback_format: bool,
) -> tuple[dict, int | None]:
    """Format selection for an AUDIO/VIDEO download, plus the audio re-encode bitrate.

    use_fallback_format is for "format not available" retries.
    """
    if use_fallback_format and not download_job.audio_only:
        # Video fallback: use generic format + FFmpeg conversion for iOS compatibility
        logger.info('Using fallback format with FFmpeg video conversion for iOS compatibility')
        return {
            'format': get_fallback_format(audio_only=False),
            'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}],
            # Force H.264 video + AAC audio for iOS compatibility
            'postprocessor_args': {
                'FFmpegVideoConvertor': [
                    '-c:v',
                    'libx264',
                    '-preset',
                    'medium',
                    '-crf',
                    '23',
                    '-c:a',
                    'aac',
                ]
            },
        }, None
    if use_fallback_format:
        # Audio fallback: FFmpegExtractAudio (added by the caller) handles conversion to m4a
        logger.info('Using fallback format for audio download')
        return {'format': get_fallback_format(audio_only=True)}, None
    if download_job.audio_only:
        # Audio-only: pick the source + re-encode target for the chosen tier. Higher
        # tiers copy a native AAC stream; lower tiers re-encode down (applied by the
        # caller's postprocessor). audio_abr_cap=None -> Best.
        audio_format, audio_pq = build_audio_selection(audio_abr_cap)
        logger.info(
            f'Using audio-only format (abr_cap={audio_abr_cap}, pq={audio_pq}): {audio_format}'
        )
        return {'format': audio_format}, audio_pq
    return {'format': get_format(fmt, quality)}, None


# The subtitle options shared by a transcript-enabled download and a transcript-only captions
# pass. VTT covers a track that has no json3; ytdlp.subtitles parses both.
_CAPTION_OPTIONS = {
    'writesubtitles': True,
    'writeautomaticsub': True,
    'subtitleslangs': ['en'],
    'subtitlesformat': 'json3/vtt',
}


# Whisper runs an English-only model, so any English stream beats every other one, and only
# then does size matter. Never 'worstaudio': it inverts the whole sort, language included,
# so on a video with YouTube auto-dubs it picks a dub. 'lang' must lead the sort too —
# '+size' alone still prefers whichever dub happens to encode smallest. HLS goes first
# within each tier: on some videos YouTube 403s every https audio stream partway through
# while the HLS one downloads in full.
_TRANSCRIPT_ONLY_AUDIO = {
    'format': (
        'bestaudio[language^=en][protocol^=m3u8]/bestaudio[language^=en]/best[language^=en]'
        '/bestaudio[protocol^=m3u8]/bestaudio/best'
    ),
    'format_sort': ['lang', '+size', '+br'],
}


def _transcript_only_options(*, captions_only: bool) -> dict:
    """Options for a transcript-only item: its captions alone, or audio for Whisper.

    Whisper resamples to 16 kHz mono itself, so the smallest English stream is all the
    fallback needs, taken as-is. Nothing either pass writes is kept once the transcript
    exists.
    """
    if not captions_only:
        return dict(_TRANSCRIPT_ONLY_AUDIO)
    return {
        **_TRANSCRIPT_ONLY_AUDIO,
        'skip_download': True,
        # Captions don't depend on media formats, so a video whose formats come back empty
        # (PO-token or SABR trouble) can still be indexed from them.
        'ignore_no_formats_error': True,
        **_CAPTION_OPTIONS,
    }


def create_ydl_options(
    download_job: DownloadJob | DownloadJobDTO,
    fmt: str = 'mp4',
    quality: str = 'best_ios',
    audio_abr_cap: int | None = None,
    sub_directory: str = '',
    extract_flat: bool = True,
    progress_hooks: list | None = None,
    use_fallback_format: bool = False,
    cookie_file: str | None = None,
    with_subtitles: bool = True,
    captions_only: bool = False,
) -> dict:
    """
    Create youtube-dl options dictionary based on the given DownloadJob or DTO.

    Args:
        download_job: The download job (ORM model or DTO).
        fmt: Desired format (default 'mp4').
        quality: Quality setting (default 'best_ios'). Video-only; ignored for audio.
        audio_abr_cap: Max audio bitrate cap in kbps for audio-only downloads
            (None = no cap / best available). See audio_quality_to_abr_cap().
        sub_directory: Directory to save the download. Defaults to ''.
        extract_flat: If True, do not resolve URLs, return immediate result. Defaults to True.
        progress_hooks: List of progress hook functions.
        use_fallback_format: If True, use generic format with FFmpeg conversion for compatibility.
        cookie_file: Path to a cookie file for this run, or None. Resolved by
            ytdlp.cookies.cookie_session, which owns the mode decision.
        with_subtitles: False leaves the captions out even when the job wants a
            transcript, for the re-run after a caption fetch failed.
        captions_only: For a TRANSCRIPT job, fetch only its captions (no media). Otherwise a
            TRANSCRIPT job gets its Whisper-fallback audio download.

    Returns:
        dict: Options dictionary for youtube-dl.
    """

    if progress_hooks is None:
        progress_hooks = []
    save_path = os.path.join(settings.storage.video_path, sub_directory)
    download_options = {
        'format_sort': ['res', '+vcodec:avc'],  # Prioritize resolution, prefer H.264
        'extract_flat': extract_flat,
        'nooverwrites': not download_job.overwrite,
        'logger': YtDlLogger(),
        'progress_hooks': progress_hooks,
        'quiet': True,
        'ffmpeg_location': '/usr/bin/ffmpeg',
        'postprocessors': [],
    }

    transcript_only = download_job.media_type == MediaType.TRANSCRIPT

    audio_pq = None  # audio-only re-encode target bitrate (set by build_audio_selection)
    if transcript_only:
        download_options.update(_transcript_only_options(captions_only=captions_only))
    else:
        format_options, audio_pq = _media_format_options(
            download_job, fmt, quality, audio_abr_cap, use_fallback_format
        )
        download_options.update(format_options)

    # Randomizing the fingerprint is cover for an anonymous request and a liability for
    # an authenticated one: yt-dlp keeps the extractor's per-client User-Agent under
    # impersonation, so a rotating TLS fingerprint under one account is a mismatch that
    # repeats on every job.
    impersonate_target = (
        get_stable_impersonate_target() if cookie_file else get_random_impersonate_target()
    )
    if impersonate_target:
        logger.debug(f'create_ydl_options using impersonation target: {impersonate_target}')
        download_options['impersonate'] = impersonate_target

    if download_job.generate_transcript and with_subtitles and not transcript_only:
        # Captions are best-effort. A video with no track goes on without one; a listed
        # track whose fetch fails raises instead, which _download_with_fallback answers by
        # re-running without them. Either way the transcript falls back to Whisper.
        download_options.update(_CAPTION_OPTIONS)

    # Read app settings (cached 60s TTL) for throttling, cookies and YouTube player client
    app_settings = settings_repo.sync_get_settings()

    download_options.update(_throttling_options(app_settings))

    if cookie_file:
        download_options['cookiefile'] = cookie_file

    if is_youtube_url(download_job.url):
        player_clients = (
            app_settings.cookies_player_client if cookie_file else app_settings.player_client
        )
        download_options['extractor_args'] = {
            'youtube': {'player_client': player_clients},
            **_get_pot_extractor_args(),
        }

    if download_job.audio_only:
        save_path = os.path.join(settings.storage.audio_path, sub_directory)

    if download_job.audio_only and not transcript_only:
        audio_format = fmt if fmt in AUDIO_FORMATS else 'm4a'
        # Normalize to the requested codec: an AAC source is copied into m4a; an opus
        # source is transcoded. audio_pq (from build_audio_selection) carries the tier's
        # target bitrate so the transcode lands at the requested size; a copied AAC
        # source ignores it.
        extract_audio_pp = {
            'key': 'FFmpegExtractAudio',
            'preferredcodec': audio_format,
        }
        if audio_pq is not None:
            extract_audio_pp['preferredquality'] = audio_pq
        download_options['postprocessors'] = [extract_audio_pp]

    # Use exist_ok=True to avoid race condition when multiple workers
    # check os.path.exists() simultaneously before any creates the directory
    os.makedirs(save_path, exist_ok=True)
    download_options['save_path'] = save_path
    return download_options


def clean_outtmpl(
    title: str,
    save_path: str,
    is_playlist: bool = False,
    playlist_index: int | None = None,
    url_hash: str | None = None,
) -> str:
    """Custom title cleaning to keep spaces. Youtube_DL doesn't have this feature natively.

    Args:
        title: The video/audio title
        save_path: Directory to save the file in
        is_playlist: Whether this is a playlist download (adds index prefix)
        playlist_index: 0-based index within the playlist
        url_hash: Optional URL hash suffix for filename uniqueness (e.g. "5a1b2c3d4e5")
    """
    to_keep = printable.replace('/', '')
    clean_title = str.strip(''.join([c for c in title if c in to_keep]))
    hash_suffix = f' [{url_hash}]' if url_hash else ''
    if is_playlist and playlist_index is not None:
        file_name = f'{(str(playlist_index).zfill(2))} - {clean_title}{hash_suffix}.%(ext)s'
    else:
        file_name = f'{clean_title}{hash_suffix}.%(ext)s'
    return os.path.join(save_path, file_name)
