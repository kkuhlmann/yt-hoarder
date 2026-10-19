import os
from datetime import timedelta

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from logger import logger
from models import (
    JobType,
    MediaDetails,
    TaskStatus,
    utc_now,
)
from orchestrator import JobCancelled, JobContext, SkipJob
from progress_publisher import publish_progress, publish_status_change
from repositories import media_details as md_repo
from repositories import settings as settings_repo
from repositories import task_records as tr_repo
from repositories import transcript_blocks as tb_repo
from repositories import users as user_repo
from schemas import DownloadJobDTO
from serializers import (
    deserialize_download_job,
    media_details_to_dto,
    serialize_download_job,
)
from utils import sanitize_folder_name
from ytdlp.cookies import cookie_session
from ytdlp.files import download_thumbnail, find_existing_file
from ytdlp.info import build_not_ready_message, is_video_ready_for_download
from ytdlp.options import (
    YtPostProcessor,
    audio_quality_to_abr_cap,
    clean_outtmpl,
    create_ydl_options,
    download_quality_to_ytdlp,
)
from ytdlp.playlists import is_repeat_download
from ytdlp.subtitles import is_subtitle_download_error
from ytdlp.urls import get_url_hash

SUBTITLE_EXTENSIONS = (
    '.vtt',
    '.srt',
    '.json3',
    '.ass',
    '.ssa',
    '.ttml',
    '.srv1',
    '.srv2',
    '.srv3',
)

SLEEP_STATUS_MESSAGE = 'Sleeping before download...'


def create_download_progress_hook(task_id: str, user_id: int | None = None, cancel_event=None):  # noqa: C901 — yt-dlp progress protocol: one branch per status/stream-type combination
    """
    Creates a progress hook function for yt-dlp that updates TaskRecord progress.

    Tracks download phase (VIDEO or AUDIO) when yt-dlp downloads separate streams.
    The download_phase field indicates which stream is currently downloading:
    - 'VIDEO': Video stream is downloading (0-100%)
    - 'AUDIO': Audio stream is downloading (0-100%)
    - None: Merged format or unknown (single progress bar)

    Args:
        task_id: The task ID to update progress for
        user_id: Optional user_id to include in published progress events
        cancel_event: Optional threading.Event; when set, the hook raises
            JobCancelled to abort the in-flight yt-dlp download between
            fragments.

    Returns:
        A progress hook function compatible with yt-dlp's progress_hooks option
    """

    def detect_stream_type(d: dict) -> str:
        """
        Detect the stream type from yt-dlp's progress dict.

        Returns: 'video', 'audio', or 'merged'
        """
        info_dict = d.get('info_dict', {})
        vcodec = info_dict.get('vcodec', 'none')
        acodec = info_dict.get('acodec', 'none')

        if vcodec != 'none' and acodec == 'none':
            result = 'video'
        elif acodec != 'none' and vcodec == 'none':
            result = 'audio'
        else:
            result = 'merged'

        logger.debug(
            f'detect_stream_type for task {task_id}: vcodec={vcodec}, acodec={acodec} -> {result}'
        )
        return result

    def progress_hook(d):
        if cancel_event is not None and cancel_event.is_set():
            msg = f'Download {task_id} cancelled'
            raise JobCancelled(msg)

        # Skip subtitle download progress events — they fire before media downloads
        # and would incorrectly trigger status changes (e.g. premature POSTPROCESSING)
        filename = d.get('filename', '')
        if any(filename.endswith(ext) for ext in SUBTITLE_EXTENSIONS):
            return

        if d['status'] == 'downloading':
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            if total > 0:
                percent = int((downloaded / total) * 100)
                stream_type = detect_stream_type(d)

                update_fields = {
                    'eta_seconds': d.get('eta', 0),
                    'percent_complete': percent,
                }

                if stream_type == 'video':
                    update_fields['download_phase'] = 'VIDEO'
                    update_fields['status_message'] = f'Downloading video... {percent}%'
                elif stream_type == 'audio':
                    update_fields['download_phase'] = 'AUDIO'
                    update_fields['status_message'] = f'Downloading audio... {percent}%'
                else:
                    # Merged stream - no phase, single progress bar
                    update_fields['download_phase'] = None
                    update_fields['status_message'] = f'Downloading... {percent}%'

                tr_repo.sync_update_one(task_id, update_fields)
                publish_progress(task_id, update_fields, user_id=user_id)

        elif d['status'] == 'finished':
            stream_type = detect_stream_type(d)
            logger.info(f'Finished downloading {stream_type} stream: {d["filename"]}')

            update_fields = {'eta_seconds': 0}

            if stream_type == 'video':
                # Video complete - keep phase as VIDEO, set to 100%
                # Phase will change to AUDIO when audio download starts
                update_fields['download_phase'] = 'VIDEO'
                update_fields['percent_complete'] = 100
                update_fields['status_message'] = 'Video complete, downloading audio...'
            elif stream_type == 'audio':
                # Audio complete - download phase done
                update_fields['download_phase'] = 'AUDIO'
                update_fields['percent_complete'] = 100
                update_fields['status_message'] = 'Download complete'
            else:
                # Merged stream - single download complete
                update_fields['percent_complete'] = 100
                update_fields['status_message'] = 'Download complete'

            tr_repo.sync_update_one(task_id, update_fields)
            publish_progress(task_id, update_fields, user_id=user_id)

    return progress_hook


def create_postprocessor_hook(task_id: str, user_id: int | None = None, cancel_event=None):
    """Create a yt-dlp postprocessor hook that sets POSTPROCESSING status
    when actual post-processing (e.g. FFmpeg merge) begins."""

    def pp_hook(d):
        if cancel_event is not None and cancel_event.is_set():
            msg = f'Download {task_id} cancelled during post-processing'
            raise JobCancelled(msg)

        if d['status'] == 'started':
            pp_name = d.get('postprocessor', 'Unknown')
            logger.info(f'Post-processing started ({pp_name}) for task {task_id}')
            update_fields = {
                'status': TaskStatus.POSTPROCESSING,
                'status_message': f'Post-processing ({pp_name})...',
                'download_phase': None,
            }
            tr_repo.sync_update_one(task_id, update_fields)
            publish_status_change(
                task_id,
                TaskStatus.POSTPROCESSING.value,
                update_fields['status_message'],
                user_id=user_id,
            )

    return pp_hook


def _resolve_live_media_details(dto: DownloadJobDTO) -> DownloadJobDTO | None:
    """Re-resolve the MediaDetails row by url+media_type at execution time.

    The payload's media_details.id was captured at populate time; by the time the
    solo downloads worker picks this task up (queue backlog + rate-limit sleeps),
    a concurrent overwrite/re-create chain may have deleted and recreated the row
    under a new id. Trusting the stale id means the download completes without
    ever writing file_path/status to the live row.

    Returns the DTO rebuilt around the live row, the DTO unchanged when it
    carries no persisted media_details, or None when the row no longer exists
    (superseded — another chain owns this URL now).
    """
    md_dto = dto.media_details
    if not md_dto or not md_dto.id:
        return dto

    live_md = md_repo.sync_get_media_details_by_url_and_media_type(
        dto.url, dto.media_type.value if dto.media_type else None
    )
    if live_md is None:
        return None
    if live_md.id == md_dto.id:
        return dto

    logger.warning(
        f'MediaDetails for {dto.url} was replaced while queued '
        f'(payload id={md_dto.id}, live id={live_md.id}); using live row'
    )
    return DownloadJobDTO(
        **dto.model_dump(exclude={'media_details', 'existing_media_details_id'}),
        media_details=media_details_to_dto(live_md),
        existing_media_details_id=live_md.id,
    )


def _handle_superseded_media(ctx: JobContext, task_id: str, dto: DownloadJobDTO) -> None:
    """Skip the download because its MediaDetails row was deleted while queued.

    A sibling chain deleted the row without recreating it (e.g. overwrite populate
    that later filtered out, or a not-ready cleanup). Marks the TaskRecord and
    downstream tasks SKIPPED, suppresses the downstream job, and raises SkipJob.
    """
    message = 'Skipped: media record was removed while this task was queued'
    logger.warning(f'Skipping download for {dto.url}: {message}')
    tr_repo.sync_update_one(
        task_id,
        {'status': TaskStatus.SKIPPED, 'status_message': message},
    )
    tr_repo.sync_mark_downstream_as_skipped(task_id)
    publish_status_change(task_id, TaskStatus.SKIPPED.value, message, user_id=dto.user_id)
    ctx.skip_downstream = True
    raise SkipJob


def _determine_download_subdirectory(dto: DownloadJobDTO) -> tuple[str, bool]:
    """Determine the subdirectory for organizing downloaded files.

    Every download gets a subfolder — subscription downloads use the subscription's
    channel name, playlist downloads use the playlist name, and one-off downloads
    use the channel name from the DTO or media details.

    Args:
        dto: The download job DTO

    Returns:
        Tuple of (subdirectory, is_playlist)
    """
    if dto.job_type in (JobType.CHANNEL_SUBSCRIPTION, JobType.PLAYLIST_SUBSCRIPTION):
        channel = dto.subscription.channel if dto.subscription else ''
        return (sanitize_folder_name(channel) if channel else 'Unknown', False)
    if dto.playlist_name:
        return (sanitize_folder_name(dto.playlist_name), True)

    channel = dto.channel or (dto.media_details.channel if dto.media_details else None)
    return (sanitize_folder_name(channel) if channel else 'Unknown', False)


def _handle_video_not_ready(
    task_id: str,
    dto: DownloadJobDTO,
    reason: str,
) -> dict:
    """Handle case where video is not ready for download (live/upcoming/post-live).

    Updates TaskRecord and MediaDetails to NOT_READY status, and marks any
    downstream tasks (e.g. transcript) as NOT_READY so they don't stay QUEUED forever.

    Args:
        task_id: The task ID
        dto: The download job DTO
        reason: Human-readable reason why video is not ready

    Returns:
        The media_details dict with status explicitly set to NOT_READY
    """
    logger.warning(f'Video not ready for {dto.url}: {reason}')
    message = build_not_ready_message(
        reason,
        dto.media_details.release_timestamp if dto.media_details else None,
        bool(dto.subscription_id),
    )
    tr_repo.sync_update_one(
        task_id,
        {'status': TaskStatus.NOT_READY, 'status_message': message},
    )
    publish_status_change(task_id, TaskStatus.NOT_READY.value, message, user_id=dto.user_id)
    # Don't leave an incomplete media_details row behind. If nothing has been
    # downloaded yet, delete it (cascades access/playlist rows) so it isn't shown
    # until the video airs and can be re-fetched with full metadata next run.
    md_dto = dto.media_details
    if md_dto and md_dto.id:
        md_orm = md_repo.sync_get_media_details_by_id(md_dto.id)
        if md_orm and not md_orm.file_path:
            md_repo.sync_delete_by_url_and_media_type(md_orm)
        elif md_orm:
            md_repo.sync_update_one(md_dto.id, {'status': TaskStatus.NOT_READY})

    # Mark downstream tasks (e.g. transcript) as NOT_READY so they don't stay QUEUED
    tr_repo.sync_mark_downstream_as_not_ready(task_id)

    result = serialize_download_job(dto).get('media_details') or {}
    # The DTO was created before the DB update, so status is stale — override it
    result['status'] = TaskStatus.NOT_READY.value
    return result


def _is_format_not_available_error(e: Exception) -> bool:
    """
    Check if the exception is a 'format not available' error from yt-dlp.

    This error occurs when the requested format (e.g., iOS-compatible H.264/AAC)
    is not available for a particular video. We can recover by using a fallback
    format and converting via FFmpeg.

    Args:
        e: The exception to check

    Returns:
        True if this is a format availability error, False otherwise
    """
    error_msg = str(e).lower()
    return 'requested format is not available' in error_msg


def _handle_file_already_exists(
    task_id: str,
    dto: DownloadJobDTO,
    existing_file: str,
) -> dict:
    """Handle case where file already exists on disk.

    Updates MediaDetails and TaskRecord to COMPLETE status.

    Args:
        task_id: The task ID
        dto: The download job DTO
        existing_file: Path to the existing file

    Returns:
        The media_details dict to return from the task
    """
    logger.info(f'File already exists at {existing_file}, skipping download')
    md_dto = dto.media_details

    # Get TaskRecord integer ID from task_id string (FK requires integer, not UUID string)
    task_record = tr_repo.sync_get_task_by_task_id(task_id)

    # For storage tracking
    try:
        file_size = os.path.getsize(existing_file)
    except OSError:
        file_size = None

    updated_md = None
    if md_dto and md_dto.id:
        updated_md = md_repo.sync_update_by_id(
            md_dto.id,
            file_path=existing_file,
            file_size_bytes=file_size,
            status=TaskStatus.COMPLETE,
            download_task_record_id=task_record.id if task_record else None,
        )
    elif md_dto:
        # MediaDetails not yet persisted (no id) - create it via upsert
        new_md = MediaDetails(
            url=md_dto.url,
            media_type=md_dto.media_type,
            channel=md_dto.channel,
            title=md_dto.title,
            playlist_index=md_dto.playlist_index,
            file_path=existing_file,
            file_size_bytes=file_size,
            release_timestamp=md_dto.release_timestamp,
            duration=md_dto.duration,
            status=TaskStatus.COMPLETE,
            download_task_record_id=task_record.id if task_record else None,
            downloaded_at=utc_now(),
            owner_id=dto.user_id,
        )
        updated_md = md_repo.sync_upsert_media_details(new_md)
    tr_repo.sync_update_one(
        task_id,
        {
            'status': TaskStatus.COMPLETE,
            'status_message': 'File already exists on disk',
            'percent_complete': 100,
        },
    )
    publish_status_change(
        task_id, TaskStatus.COMPLETE.value, 'File already exists on disk', user_id=dto.user_id
    )

    if updated_md:
        return media_details_to_dto(updated_md).model_dump()

    # Fallback to original DTO if no media_details
    return serialize_download_job(dto).get('media_details') or {}


def _rate_limit_sleep(ctx: JobContext, task_id: str, dto: DownloadJobDTO) -> None:
    """Sleep between downloads for rate limiting, with cancellation polling.

    Only applies to subscription/playlist downloads — one-off downloads skip this.
    The cancel event interrupts the sleep immediately; a TaskRecord poll every
    5 seconds remains as a fallback (cancellation written to the DB by a path
    that could not signal the event).

    Args:
        ctx: The job context (cancel event + downstream control)
        task_id: The task ID
        dto: The download job DTO
    """
    if dto.job_type not in (
        JobType.PLAYLIST_SUBSCRIPTION,
        JobType.CHANNEL_SUBSCRIPTION,
        JobType.PLAYLIST_DOWNLOAD,
    ):
        return

    app_settings = settings_repo.sync_get_settings()
    sleep_seconds = app_settings.download_sleep_seconds
    if sleep_seconds <= 0:
        return

    # The wake time, not the duration: it is what lets the UI render a countdown that
    # keeps ticking between events and comes back correct after a page reload.
    wake_at = utc_now() + timedelta(seconds=sleep_seconds)
    tr_repo.sync_update_one(
        task_id, {'status_message': SLEEP_STATUS_MESSAGE, 'sleep_until': wake_at}
    )
    publish_status_change(
        task_id,
        TaskStatus.IN_PROGRESS.value,
        SLEEP_STATUS_MESSAGE,
        user_id=dto.user_id,
        fields={'sleep_until': wake_at.isoformat()},
    )
    logger.info(f'Rate limiting: sleeping {sleep_seconds}s before download')
    for i in range(sleep_seconds):
        if ctx.cancel_event.wait(1):
            logger.info(f'Task {task_id} cancelled during rate-limiting sleep, exiting')
            ctx.skip_downstream = True
            msg = f'Download {task_id} cancelled during rate-limit sleep'
            raise JobCancelled(msg)
        if (i + 1) % 5 == 0:
            record = tr_repo.sync_get_task_by_task_id(task_id)
            if record and record.status == TaskStatus.CANCELLED:
                logger.info(f'Task {task_id} cancelled during rate-limiting sleep, exiting')
                ctx.skip_downstream = True
                msg = f'Download {task_id} cancelled during rate-limit sleep'
                raise JobCancelled(msg)

    # Only the normal exit clears it. The cancel paths raise, and a deadline left on a
    # CANCELLED row is inert — the display state is gated on the row being IN_PROGRESS.
    tr_repo.sync_update_one(task_id, {'sleep_until': None})


def _format_gb(num_bytes: int) -> str:
    return f'{num_bytes / 1_000_000_000:.1f} GB'


def _check_storage_quota(dto: DownloadJobDTO) -> str | None:
    """Check whether the job owner is at or over their storage quota.

    Returns a human-readable skip message if the quota is exhausted, or None
    if the download may proceed. Unowned jobs — `Subscription.user_id` is
    nullable, so a subscription-sourced job can carry no user — and users
    without a storage limit are always allowed.
    """
    if dto.user_id is None:
        return None
    user = user_repo.sync_get_user_by_id(dto.user_id)
    if user is None or user.storage_limit_bytes is None:
        return None
    usage = user_repo.sync_get_user_storage_usage(dto.user_id)
    if usage >= user.storage_limit_bytes:
        return (
            f'Storage limit reached ({_format_gb(usage)} of '
            f'{_format_gb(user.storage_limit_bytes)} used)'
        )
    return None


def _handle_quota_exceeded(
    ctx: JobContext, task_id: str, dto: DownloadJobDTO, message: str
) -> None:
    """Skip the download because the owner's storage quota is exhausted.

    Marks the TaskRecord and MediaDetails as SKIPPED (terminal — no retry),
    marks downstream tasks (e.g. transcript) as SKIPPED, suppresses the
    downstream job, and raises SkipJob so no success/failure hooks run. The
    next subscription cycle re-evaluates SKIPPED media, so downloads resume
    automatically once the user frees space.
    """
    logger.warning(f'Skipping download for {dto.url}: {message}')
    tr_repo.sync_update_one(
        task_id,
        {'status': TaskStatus.SKIPPED, 'status_message': message},
    )
    md_dto = dto.media_details
    if md_dto and md_dto.id:
        md_repo.sync_update_one(md_dto.id, {'status': TaskStatus.SKIPPED})
    tr_repo.sync_mark_downstream_as_skipped(task_id)
    publish_status_change(task_id, TaskStatus.SKIPPED.value, message, user_id=dto.user_id)
    ctx.skip_downstream = True
    raise SkipJob


def _next_attempt(
    e: DownloadError, dto: DownloadJobDTO, *, use_fallback_format: bool, with_subtitles: bool
) -> tuple[bool, bool]:
    """The (use_fallback_format, with_subtitles) to re-run with after `e`, else re-raise it.

    Each fallback is taken at most once, so a download makes at most three attempts.
    """
    if with_subtitles and is_subtitle_download_error(e):
        logger.warning(
            f'Captions failed to download for {dto.url}; retrying without them, '
            f'so the transcript falls back to Whisper: {e}'
        )
        return use_fallback_format, False
    if not use_fallback_format and _is_format_not_available_error(e):
        logger.warning(f'Format not available, retrying with fallback: {e}')
        return True, with_subtitles
    raise e


def _download_with_fallback(
    ydl: YoutubeDL,
    ydl_opts: dict,
    dto: DownloadJobDTO,
    md_orm,
    download_info: dict,
    sub_directory: str,
    progress_hook,
    pp_hook,
    *,
    cookie_file: str | None,
) -> None:
    """Download using extracted info, re-running with fallback options on failure.

    First attempts process_ie_result (reuses already-extracted info, avoids a second
    network call). Two failures are recoverable, each by a fresh YoutubeDL and a full
    download: a requested format that isn't available (fallback format + FFmpeg
    conversion), and a caption track that failed to download (no subtitles, so the
    transcript job uses Whisper instead).

    Args:
        ydl: The primary YoutubeDL instance
        ydl_opts: Options dict used by the primary instance
        dto: The download job DTO
        md_orm: The MediaDetails ORM instance (for YtPostProcessor)
        download_info: Extracted video info dict from ydl.extract_info()
        sub_directory: Subdirectory for file organization
        progress_hook: Download progress callback
        pp_hook: Postprocessor hook callback
        cookie_file: The run's private cookie-file copy, or None. Shared with the
            primary instance so both write to the same disposable file.
    """
    try:
        ydl.process_ie_result(download_info, download=True)
    except DownloadError as e:
        use_fallback_format, with_subtitles = _next_attempt(
            e, dto, use_fallback_format=False, with_subtitles=True
        )
    else:
        return

    md_dto = dto.media_details
    while True:
        ydl_opts_retry = create_ydl_options(
            dto,
            quality=download_quality_to_ytdlp(dto.download_quality),
            audio_abr_cap=audio_quality_to_abr_cap(dto.audio_quality),
            sub_directory=sub_directory,
            extract_flat=False,
            use_fallback_format=use_fallback_format,
            cookie_file=cookie_file,
            with_subtitles=with_subtitles,
        )
        ydl_opts_retry['outtmpl'] = ydl_opts['outtmpl']
        ydl_opts_retry['progress_hooks'] = [progress_hook]
        ydl_opts_retry['postprocessor_hooks'] = [pp_hook]
        try:
            with YoutubeDL(ydl_opts_retry) as ydl_retry:
                if md_orm:
                    ydl_retry.add_post_processor(YtPostProcessor(md_orm.id))
                ydl_retry.download([md_dto.url if md_dto else dto.url])
        except DownloadError as e:
            use_fallback_format, with_subtitles = _next_attempt(
                e, dto, use_fallback_format=use_fallback_format, with_subtitles=with_subtitles
            )
        else:
            return


def _post_download_update(dto: DownloadJobDTO, download_info: dict | None) -> dict:
    """Refresh MediaDetails from DB after download and fetch thumbnail.

    The DTO is stale after download because YtPostProcessor updated file_path
    directly on the ORM model. This re-fetches the current state and optionally
    downloads a thumbnail sidecar file.

    Args:
        dto: The download job DTO (stale — used for fallback only)
        download_info: Extracted video info dict (for thumbnail URL)

    Returns:
        Serialized media_details dict for the job's return value
    """
    md_dto = dto.media_details
    if md_dto and md_dto.id:
        updated_md = md_repo.sync_get_media_details_by_id(md_dto.id)
        # Backfill duration from the full extract_info if it wasn't set at populate
        # time (e.g. flat extraction omitted it for a since-released premiere).
        if updated_md and updated_md.duration is None and download_info:
            dur = download_info.get('duration')
            if dur is not None:
                md_repo.sync_update_one(updated_md.id, {'duration': dur})
                updated_md = md_repo.sync_get_media_details_by_id(updated_md.id)
        if updated_md and updated_md.file_path:
            # Download thumbnail as sidecar file (non-critical — failures are logged and ignored)
            thumbnail_url = download_info.get('thumbnail') if download_info else None
            if thumbnail_url:
                thumb_path = download_thumbnail(thumbnail_url, updated_md.file_path)
                if thumb_path:
                    md_repo.sync_update_one(updated_md.id, {'thumbnail_path': thumb_path})
                    updated_md = md_repo.sync_get_media_details_by_id(updated_md.id)
        if updated_md:
            return media_details_to_dto(updated_md).model_dump()

    # Fallback to original DTO if no media_details
    return serialize_download_job(dto).get('media_details') or {}


def _check_repeat_download(dto: DownloadJobDTO) -> bool:
    """Check if this download has already been completed. Returns True if it should be skipped."""
    md_dto = dto.media_details
    if dto.overwrite or not md_dto or not md_dto.id:
        return False
    md_orm = md_repo.sync_get_media_details_by_id(md_dto.id)
    return bool(md_orm and is_repeat_download(md_orm))


def _build_download_options(
    dto: DownloadJobDTO,
    *,
    sub_directory: str,
    is_playlist: bool,
    progress_hook,
    pp_hook,
    cookie_file: str | None = None,
) -> dict:
    """Build yt-dlp options with output template and hooks attached."""
    ydl_opts = create_ydl_options(
        dto,
        quality=download_quality_to_ytdlp(dto.download_quality),
        audio_abr_cap=audio_quality_to_abr_cap(dto.audio_quality),
        sub_directory=sub_directory,
        extract_flat=False,
        cookie_file=cookie_file,
    )
    url_hash = get_url_hash(dto.url)
    md_dto = dto.media_details
    ydl_opts['outtmpl'] = clean_outtmpl(
        title=md_dto.title,
        save_path=ydl_opts['save_path'],
        is_playlist=is_playlist,
        playlist_index=md_dto.playlist_index,
        url_hash=url_hash,
    )
    ydl_opts['progress_hooks'] = [progress_hook]
    ydl_opts['postprocessor_hooks'] = [pp_hook]
    return ydl_opts


def _check_file_exists_on_disk(dto: DownloadJobDTO, outtmpl: str) -> tuple[str | None, bool]:
    """Check if the download target already exists on disk.

    Returns (existing_file_path, should_skip_transcript).
    """
    if dto.overwrite:
        return None, False
    existing_file = find_existing_file(outtmpl, dto.audio_only)
    if not existing_file:
        return None, False
    md_dto = dto.media_details
    should_skip_transcript = not dto.generate_transcript or tb_repo.sync_has_transcript_blocks(
        md_dto.id if md_dto else None
    )
    return existing_file, should_skip_transcript


def run_download_job(ctx: JobContext, dl_job: dict) -> dict:  # noqa: C901 — the download state machine; branches are distinct terminal outcomes
    """Download one video/audio via yt-dlp."""
    task_id = ctx.task_id
    dto = deserialize_download_job(dl_job)
    progress_hook = create_download_progress_hook(
        task_id, user_id=dto.user_id, cancel_event=ctx.cancel_event
    )
    pp_hook = create_postprocessor_hook(task_id, user_id=dto.user_id, cancel_event=ctx.cancel_event)

    if not dto.generate_transcript:
        ctx.skip_downstream = True

    logger.debug(f'Starting download for {dto}')

    try:
        # Re-resolve MediaDetails by url+media_type — the payload id may be stale
        # if a concurrent chain replaced the row while this task sat in the queue.
        resolved_dto = _resolve_live_media_details(dto)
        if resolved_dto is None:
            _handle_superseded_media(ctx, task_id, dto)
        dto = resolved_dto
        md_dto = dto.media_details

        tr_repo.sync_update_one(
            task_id,
            {
                'title': md_dto.title if md_dto else None,
                'channel': md_dto.channel if md_dto else None,
                'release_timestamp': md_dto.release_timestamp if md_dto else None,
                'media_type': md_dto.media_type if md_dto else None,
            },
        )

        sub_directory, is_playlist = _determine_download_subdirectory(dto)

        if _check_repeat_download(dto):
            ctx.skip_downstream = True
            # skip_downstream only suppresses the in-memory enqueue; without a terminal
            # write the transcript row sits QUEUED forever with nothing behind it.
            tr_repo.sync_skip_downstream_transcripts(
                task_id, 'Skipped - media was already downloaded'
            )
            return serialize_download_job(dto).get('media_details') or {}

        with cookie_session(is_retry=ctx.attempt > 0) as cookie_file:
            ydl_opts = _build_download_options(
                dto,
                sub_directory=sub_directory,
                is_playlist=is_playlist,
                progress_hook=progress_hook,
                pp_hook=pp_hook,
                cookie_file=cookie_file,
            )

            youtube_args = ydl_opts.get('extractor_args', {}).get('youtube', {})
            logger.info(
                f'Download {task_id}: attempt={ctx.attempt} cookies={bool(cookie_file)} '
                f'clients={youtube_args.get("player_client")}'
            )

            existing_file, should_skip_transcript = _check_file_exists_on_disk(
                dto, ydl_opts['outtmpl']
            )
            if existing_file:
                if should_skip_transcript:
                    ctx.skip_downstream = True
                    tr_repo.sync_skip_downstream_transcripts(
                        task_id, 'Skipped - transcript already exists'
                    )
                return _handle_file_already_exists(task_id, dto, existing_file)

            # Quota check: skip (terminal, no retry) if the owner is at/over their storage limit
            quota_message = _check_storage_quota(dto)
            if quota_message:
                _handle_quota_exceeded(ctx, task_id, dto, quota_message)

            # Rate-limit sleep: only for subscription/playlist downloads, and only when
            # we're actually about to download (file-existence checks above already returned early)
            _rate_limit_sleep(ctx, task_id, dto)

            # Fetch ORM model for YtPostProcessor (which updates the DB)
            md_orm = None
            if md_dto and md_dto.id:
                md_orm = md_repo.sync_get_media_details_by_id(md_dto.id)

            # Single YoutubeDL instance: extract_info for live check, then
            # process_ie_result to download using the already-extracted info.
            # NOTE: process_ie_result is a public method on YoutubeDL that download()
            # calls internally after extract_info. It has been stable across yt-dlp
            # versions, but is not part of yt-dlp's documented stable API.
            # If a future yt-dlp update removes it, fall back to using ydl.download().
            with YoutubeDL(ydl_opts) as ydl:
                if md_orm:
                    ydl.add_post_processor(YtPostProcessor(md_orm.id))

                download_info = ydl.extract_info(dto.url, download=False)
                logger.debug(f'download_info: {download_info}')

                is_ready, reason = is_video_ready_for_download(download_info)
                if not is_ready:
                    ctx.skip_downstream = True
                    return _handle_video_not_ready(task_id, dto, reason)

                _download_with_fallback(
                    ydl,
                    ydl_opts,
                    dto,
                    md_orm,
                    download_info,
                    sub_directory,
                    progress_hook,
                    pp_hook,
                    cookie_file=cookie_file,
                )

        return _post_download_update(dto, download_info)

    except (SkipJob, JobCancelled):
        # Deliberate skip (quota/superseded) or cancellation — must not be
        # converted into a retry by the catch-all below.
        raise
    except Exception as e:
        if ctx.cancelled():
            # yt-dlp may wrap the JobCancelled raised inside a hook — a set
            # cancel event means this failure IS the cancellation.
            logger.info(f'Download {task_id} aborted by cancellation')
            msg = f'Download {task_id} cancelled'
            raise JobCancelled(msg) from e
        logger.exception(f'Download {task_id} failed, retrying')
        ctx.retry(e)
