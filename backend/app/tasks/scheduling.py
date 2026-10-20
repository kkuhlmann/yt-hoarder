import uuid

from sqlalchemy.exc import IntegrityError

from logger import logger
from models import MediaType, TaskRecord, TaskStatus, TaskType
from orchestrator import JobContext, retry_transient_db
from progress_publisher import publish_status_change
from repositories import media_details as md_repo
from repositories import settings as settings_repo
from repositories import task_records as tr_repo
from repositories.task_records import DIRECT_DOWNLOAD_PRIORITY
from services.cleanup import (
    cleanup_incomplete_downloads,
    cleanup_old_temp_files,
    cleanup_orphaned_thumbnails,
    cleanup_orphaned_transcript_staging,
)
from ytdlp.files import kept_thumbnails_dir
from ytdlp.urls import get_url_hash


def cleanup_temp_files_impl() -> dict:
    """
    Clean up temporary files from transcript generation and incomplete downloads.
    Removes:
    - Transcript chunk directories and audio transcript files older than configured hours
    - Incomplete download files (*.part, *.ytdl) older than 1 hour
    - Transcript-only staging files older than configured hours that no item still needs
    - Kept thumbnails older than configured hours that no media row points at
    """
    logger.info('Running periodic cleanup of temporary files...')
    app_settings = settings_repo.sync_get_settings()
    age_hours = app_settings.cleanup_age_hours

    temp_deleted = cleanup_old_temp_files(age_hours)
    partial_deleted = cleanup_incomplete_downloads(age_hours=1)
    in_flight = {get_url_hash(url) for url in md_repo.sync_get_in_flight_transcript_only_urls()}
    staging_deleted = cleanup_orphaned_transcript_staging(age_hours, in_flight)
    referenced = md_repo.sync_get_thumbnail_paths_under(kept_thumbnails_dir())
    thumbnails_deleted = cleanup_orphaned_thumbnails(age_hours, referenced)

    total_deleted = temp_deleted + partial_deleted + staging_deleted + thumbnails_deleted
    logger.info(
        f'Cleanup task completed: {total_deleted} items removed '
        f'({temp_deleted} temp files, {partial_deleted} incomplete downloads, '
        f'{staging_deleted} transcript staging files, {thumbnails_deleted} kept thumbnails)'
    )
    return {
        'deleted_count': total_deleted,
        'temp_files_deleted': temp_deleted,
        'incomplete_downloads_deleted': partial_deleted,
        'transcript_staging_deleted': staging_deleted,
        'thumbnails_deleted': thumbnails_deleted,
        'age_hours': age_hours,
    }


def run_cleanup_job(_ctx: JobContext) -> dict:
    """Orchestrator body for the daily temp-file cleanup."""
    return cleanup_temp_files_impl()


def expand_playlists_impl(dl_jobs: list[dict]) -> list[dict]:
    """
    Expands playlist URLs into individual video jobs when download_playlist=True.
    Used for API-initiated downloads that include playlists.
    """
    from serializers import deserialize_download_job, serialize_download_job
    from ytdlp.playlists import populate_playlist_jobs
    from ytdlp.urls import normalize_playlist_url

    if not dl_jobs:
        return []

    expanded_jobs = []
    for dl_job in dl_jobs:
        dto = deserialize_download_job(dl_job)

        playlist_url = normalize_playlist_url(dto.url)
        if playlist_url and dto.download_playlist:
            logger.info(f'Expanding playlist: {playlist_url}')
            video_jobs = populate_playlist_jobs(dl_job, playlist_url)
            expanded_jobs.extend(video_jobs)
        else:
            expanded_jobs.append(serialize_download_job(dto))

    if expanded_jobs:
        logger.info(f'Expanded to {len(expanded_jobs)} jobs from {len(dl_jobs)} original')
    return expanded_jobs


def _adopt_or_create_placeholder(job: dict) -> None:
    """Give an expanded playlist video its own RESOLVING row in the tasks table.

    Created *after* filter_completed_downloads_impl on purpose: a 500-video playlist
    where 480 are already downloaded should surface 20 rows, not 500 that immediately
    flip to SKIPPED. Jobs submitted directly through POST /ytdl/ already carry a
    placeholder from the router and are left alone.

    The payload is stamped before the insert, since it is serialized at commit — an id
    added afterwards never reaches pending_payload, and a restart-resumed populate would
    insert a second download row against the slot this one still holds.
    """
    if job.get('placeholder_task_id'):
        return

    media_type = job.get('media_type')
    task_id = str(uuid.uuid4())
    job['placeholder_task_id'] = task_id
    message = 'Fetching video metadata...'
    record = TaskRecord(
        task_id=task_id,
        task_type=TaskType.DOWNLOAD,
        status=TaskStatus.RESOLVING,
        status_message=message,
        title=job['url'],
        media_type=MediaType(media_type) if media_type else None,
        download_job_url=job['url'],
        pending_payload=job,
        priority=DIRECT_DOWNLOAD_PRIORITY,
        user_id=job.get('user_id'),
    )
    try:
        tr_repo.sync_insert_task(record)
    except IntegrityError:
        logger.info(f'No placeholder for {job["url"]}: an active task already owns it')
        job['placeholder_task_id'] = None
        return

    publish_status_change(task_id, TaskStatus.RESOLVING.value, message, user_id=job.get('user_id'))


def _retire_playlist_placeholder(dl_jobs: list[dict], expanded_count: int) -> None:
    """Hide the submitted playlist's own row once its per-video children exist.

    The playlist URL never becomes a download of its own, so its placeholder would
    otherwise sit in the table forever holding that URL's active-unique slot.
    """
    for job in dl_jobs:
        if job.get('download_playlist'):
            tr_repo.sync_retire_placeholder(
                job.get('placeholder_task_id'),
                TaskStatus.SKIPPED,
                f'Expanded into {expanded_count} video task(s)',
                soft_delete=True,
            )


@retry_transient_db
def _fan_out_download_chains(dl_jobs: list[dict], unclaimed: set[str]) -> dict:
    """expand_playlists → filter_completed → one populate job per surviving download.

    Discards each placeholder id from `unclaimed` once its populate job is queued, so the
    caller's cleanup can tell a stranded row from one another job now owns.
    """
    from orchestrator import POPULATE_JOB, JobSpec, orch
    from tasks.media import filter_completed_downloads_impl

    jobs = expand_playlists_impl(dl_jobs)
    jobs = filter_completed_downloads_impl(jobs) if jobs else []

    # Keyed off the submission, not the expansion result, so an empty or fully-filtered
    # playlist still retires its row instead of leaving it stuck in RESOLVING.
    _retire_playlist_placeholder(dl_jobs, len(jobs))
    if not jobs:
        return {'jobs_started': 0}

    # Priority is not inherited: JobContext doesn't carry it and the orchestrator
    # copies only args into downstream specs, so every fan-out site sets it.
    for job in jobs:
        _adopt_or_create_placeholder(job)
        task_id = job.get('placeholder_task_id')
        if task_id:
            unclaimed.add(task_id)
        orch.submit_from_thread(
            JobSpec(
                job_name=POPULATE_JOB,
                args=(job,),
                tracked=False,
                priority=DIRECT_DOWNLOAD_PRIORITY,
                user_id=job.get('user_id'),
            )
        )
        unclaimed.discard(task_id)

    logger.info(f'Started {len(jobs)} download chains')
    return {'jobs_started': len(jobs)}


def run_direct_download_pipeline(_ctx: JobContext, dl_jobs: list[dict]) -> dict:
    """Orchestrator body: API-initiated downloads as plain control flow.

    Deliberately not wrapped in guard_resolving_placeholders: this body *delegates* each
    RESOLVING row to a POPULATE_JOB and returns long before that job adopts it, so a
    blanket retire-on-exit would kill every chain it just started. It retires only what it
    never handed off.

    The retry sits inside this cleanup rather than around it, because a retire between DB
    attempts would leave the next attempt handing off a row populate can no longer adopt.

    `unclaimed` holds task_id *values*, not job dicts: expand_playlists_impl round-trips
    even a non-playlist job through serialize_download_job, so the dict that gets submitted
    is never the one that arrived.
    """
    unclaimed = {job['placeholder_task_id'] for job in dl_jobs if job.get('placeholder_task_id')}
    try:
        return _fan_out_download_chains(dl_jobs, unclaimed)
    finally:
        for task_id in unclaimed:
            tr_repo.sync_retire_placeholder(
                task_id, TaskStatus.SKIPPED, 'Could not resolve this video'
            )
