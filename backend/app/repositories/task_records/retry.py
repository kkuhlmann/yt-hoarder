# --- Downstream marking + retry/dispatch orchestration ---

import asyncio
import uuid

from sqlalchemy import and_, text
from sqlalchemy.orm import joinedload
from sqlmodel import select

from database import db
from logger import logger
from models import MediaDetails, MediaType, TaskRecord, TaskStatus, TaskType
from progress_publisher import publish_status_change
from serializers import (
    download_job_to_dto,
    media_details_to_dto,
    serialize_download_job,
    serialize_media_details,
)
from ytdlp.files import find_staged_audio, transcript_staging_base
from ytdlp.subtitles import find_subtitle_file

from ..errors import InvalidStateError, NotFoundError
from .crud import DIRECT_DOWNLOAD_PRIORITY, SUBSCRIPTION_DOWNLOAD_PRIORITY, get_task_by_id
from .statements import mark_downstream_stmt
from .sync_ops import sync_get_next_queue_sequence, sync_update_one

# Statuses that allow direct manual retry of a task. RETRY is included so a
# user can force an automatically-scheduled retry to run immediately instead
# of waiting out its next_retry_at backoff.
RETRYABLE_STATUSES = [TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.RETRY]

DOWNSTREAM_RETRYABLE_STATUSES = [TaskStatus.UPSTREAM_FAILED, TaskStatus.CANCELLED]


async def mark_downstream_as_cancelled(task_id: str) -> int:
    """Mark all tasks that depend on this task_id as cancelled."""
    async with db.get_async_session() as session:
        result = await session.execute(
            mark_downstream_stmt(
                TaskStatus.CANCELLED,
                'Cancelled due to upstream task cancellation',
            ),
            {'upstream_tid': task_id},
        )
        modified_count = result.rowcount
        await session.commit()
        logger.info(f'Marked {modified_count} downstream tasks as CANCELLED')
        return modified_count


async def _fetch_download_job_serialized(session, task_record_id: int, label: str) -> dict:
    """Fetch the DownloadJob for a download TaskRecord and return serialized data.

    Shared by retry and prioritize flows.

    Args:
        session: Active async database session
        task_record_id: The TaskRecord.id (primary key)
        label: Description for error messages (e.g., 'download task 123')

    Returns:
        Serialized DownloadJob dict ready for dispatch
    """
    md_stmt = (
        select(MediaDetails)
        .where(MediaDetails.download_task_record_id == task_record_id)
        .options(joinedload(MediaDetails.download_jobs))
    )
    md_result = await session.execute(md_stmt)
    media_details = md_result.unique().scalar_one_or_none()

    if not media_details:
        msg = f'No MediaDetails found for {label}'
        raise NotFoundError(msg)

    if not media_details.download_jobs or len(media_details.download_jobs) == 0:
        msg = f'No DownloadJob found for MediaDetails {media_details.id}'
        raise NotFoundError(msg)

    download_job = media_details.download_jobs[0]
    download_job_dto = download_job_to_dto(download_job, media_details=media_details)
    return serialize_download_job(download_job_dto)


async def _fetch_transcript_task_serialized(session, task_record_id: int, label: str) -> dict:
    """Fetch the MediaDetails for a transcript TaskRecord and return serialized data.

    The transcript analog of _fetch_download_job_serialized.

    Args:
        session: Active async database session
        task_record_id: The TaskRecord.id (primary key)
        label: Description for error messages (e.g., 'transcript task abc-123')

    Returns:
        Serialized MediaDetails dict ready for dispatch
    """
    md_stmt = select(MediaDetails).where(MediaDetails.transcript_task_record_id == task_record_id)
    md_result = await session.execute(md_stmt)
    media_details = md_result.scalar_one_or_none()

    if not media_details:
        msg = f'No MediaDetails found for {label}'
        raise NotFoundError(msg)

    md_dto = media_details_to_dto(media_details)
    return serialize_media_details(md_dto)


async def _fetch_sprites_task_serialized(session, task_record: TaskRecord) -> dict:
    """Build the sprite job payload for a SPRITE_GENERATION TaskRecord.

    Resolved from download_job_url + media_type; sprite rows carry no FK back to
    MediaDetails. force is always True — an explicit retry must regenerate, or the
    body would just skip on the sheet already sitting on disk.
    """
    md_stmt = select(MediaDetails).where(
        and_(
            MediaDetails.url == task_record.download_job_url,
            MediaDetails.media_type == task_record.media_type,
        )
    )
    md_result = await session.execute(md_stmt)
    media_details = md_result.scalar_one_or_none()

    if not media_details:
        msg = f'No MediaDetails found for sprite task {task_record.task_id}'
        raise NotFoundError(msg)

    return {
        'media_details_id': media_details.id,
        'user_id': task_record.user_id,
        'force': True,
    }


async def _replace_upstream_task_ids(old_task_id: str, new_task_id: str) -> list[TaskRecord]:
    """Update all downstream tasks' upstream_task_ids to reference a new task_id.

    Shared by retry and prioritize flows. Prevents stale references after
    task ID changes.

    Args:
        old_task_id: The previous task ID being replaced
        new_task_id: The new task ID

    Returns:
        List of downstream TaskRecord objects (with updated upstream_task_ids)
    """
    async with db.get_async_session() as session:
        stmt = select(TaskRecord).where(
            text('task_records.upstream_task_ids::jsonb ? :old_task_id')
        )
        result = await session.execute(stmt, {'old_task_id': old_task_id})
        downstream_tasks = result.scalars().all()

        if downstream_tasks:
            logger.info(f'Updating upstream references in {len(downstream_tasks)} downstream tasks')
            for dt in downstream_tasks:
                if dt.upstream_task_ids:
                    dt.upstream_task_ids = [
                        new_task_id if tid == old_task_id else tid for tid in dt.upstream_task_ids
                    ]
            await session.commit()

        return downstream_tasks


async def _cancel_and_reassign_task_id(task_record: TaskRecord) -> tuple[str, str]:
    """Cancel any queued/running job under the old id and assign a fresh UUID.

    Must be called within an active database session context, as it mutates
    the task_record object (caller is responsible for committing).

    Returns:
        (old_task_id, new_task_id)
    """
    from orchestrator import orch

    old_task_id = task_record.task_id
    new_task_id = str(uuid.uuid4())

    outcome = await orch.cancel(old_task_id)
    logger.info(f'Cancelled old job {old_task_id} ({outcome})')

    task_record.task_id = new_task_id
    return old_task_id, new_task_id


def _reset_task_for_requeue(task_record: TaskRecord, status_message: str):
    """Reset a TaskRecord's status fields for re-dispatch.

    Sets status to QUEUED, percent_complete to 0, eta_seconds to None,
    and status_message to the provided value. The automatic-retry counters
    reset too — a manual retry starts a fresh attempt series.

    Must be called within an active database session context.
    """
    task_record.status = TaskStatus.QUEUED
    task_record.percent_complete = 0
    task_record.status_message = status_message
    task_record.eta_seconds = None
    task_record.retry_count = 0
    task_record.next_retry_at = None


def dispatch_download_chain(
    download_data: dict,
    download_task_id: str,
    transcript_task_id: str | None = None,
    priority: int = 5,
    download_status_msg: str = 'Queued',
    transcript_status_msg: str | None = None,
    download_queue_sequence: int | None = None,
    user_id: int | None = None,
):
    """Create and dispatch a download job, optionally chained with a transcript job.

    Handles JobSpec creation, orchestrator submission, queue_sequence
    assignment, and SSE status publishing. Sync — safe from lane threads;
    async callers wrap it in asyncio.to_thread.

    Args:
        download_data: Serialized download job dict
        download_task_id: Task ID for the download job
        transcript_task_id: Optional task ID for a chained transcript job
        priority: Priority (0=highest, 9=lowest)
        download_status_msg: SSE status message for the download task
        transcript_status_msg: SSE status message for the transcript task.
            If None, transcript queue_sequence and SSE publish are skipped.
        download_queue_sequence: If provided, use this value for the download task's
            queue_sequence instead of taking the next sequence value
        user_id: Optional user_id for SSE event filtering (non-admin users only see their events)
    """
    from orchestrator import DOWNLOAD_JOB, TRANSCRIPT_JOB, JobSpec, orch

    # Get queue_sequence BEFORE submitting to avoid race conditions where
    # two concurrent dispatches could get sequences in a different order than dispatch
    dl_seq = (
        download_queue_sequence
        if download_queue_sequence is not None
        else sync_get_next_queue_sequence()
    )
    t_seq = sync_get_next_queue_sequence() if transcript_task_id else None

    # Persist queue metadata and publish QUEUED *before* submitting: the
    # downloads lane can pick the job up instantly, and its IN_PROGRESS update
    # must not be overwritten by a late QUEUED write/publish. queue_sequence also
    # doubles as a was-dispatched marker for startup recovery.
    sync_update_one(download_task_id, {'queue_sequence': dl_seq, 'priority': priority})
    publish_status_change(
        download_task_id, TaskStatus.QUEUED.value, download_status_msg, user_id=user_id
    )

    if transcript_task_id and transcript_status_msg is not None:
        sync_update_one(transcript_task_id, {'queue_sequence': t_seq, 'priority': priority})
        publish_status_change(
            transcript_task_id, TaskStatus.QUEUED.value, transcript_status_msg, user_id=user_id
        )

    downstream = None
    if transcript_task_id:
        # Enqueued into the ml lane only when the download succeeds; the
        # wrapper passes the download's return value as its argument.
        downstream = JobSpec(
            job_name=TRANSCRIPT_JOB,
            task_id=transcript_task_id,
            priority=priority,
            queue_sequence=t_seq,
            user_id=user_id,
        )

    orch.submit_from_thread(
        JobSpec(
            job_name=DOWNLOAD_JOB,
            args=(download_data,),
            task_id=download_task_id,
            priority=priority,
            queue_sequence=dl_seq,
            user_id=user_id,
            downstream=downstream,
        )
    )

    transcript_suffix = f' → transcript {transcript_task_id}' if transcript_task_id else ''
    logger.info(f'Dispatched download task {download_task_id}{transcript_suffix}')


def _dispatch_transcript_task(
    transcript_data: dict,
    task_id: str,
    status_msg: str,
    priority: int = 5,
    user_id: int | None = None,
):
    """Dispatch a standalone transcript job (not chained after a download).

    Used when retrying a TRANSCRIPT_GENERATION task independently.
    """
    from orchestrator import TRANSCRIPT_JOB, JobSpec, orch

    seq = sync_get_next_queue_sequence()
    sync_update_one(task_id, {'queue_sequence': seq, 'priority': priority})
    publish_status_change(task_id, TaskStatus.QUEUED.value, status_msg, user_id=user_id)
    orch.submit_from_thread(
        JobSpec(
            job_name=TRANSCRIPT_JOB,
            args=(transcript_data,),
            task_id=task_id,
            priority=priority,
            queue_sequence=seq,
            user_id=user_id,
        )
    )
    logger.info(f'Dispatched transcript task {task_id}')


def _dispatch_sprites_task(
    sprites_data: dict,
    task_id: str,
    status_msg: str,
    priority: int = 5,
    user_id: int | None = None,
):
    """Dispatch a sprite-generation job. Used when retrying a SPRITE_GENERATION task."""
    from orchestrator import SPRITES_JOB, JobSpec, orch

    seq = sync_get_next_queue_sequence()
    sync_update_one(task_id, {'queue_sequence': seq, 'priority': priority})
    publish_status_change(task_id, TaskStatus.QUEUED.value, status_msg, user_id=user_id)
    orch.submit_from_thread(
        JobSpec(
            job_name=SPRITES_JOB,
            args=(sprites_data,),
            task_id=task_id,
            priority=priority,
            queue_sequence=seq,
            user_id=user_id,
        )
    )
    logger.info(f'Dispatched sprite task {task_id}')


async def _validate_task_for_retry(session, task_id: str) -> TaskRecord:
    """Fetch a TaskRecord and validate it is eligible for retry.

    Must receive the caller's session (not open its own) because the returned
    TaskRecord needs to stay attached for subsequent mutations before commit.

    Args:
        session: Active async database session
        task_id: The task ID to look up

    Returns:
        The validated TaskRecord (attached to session)

    Raises:
        NotFoundError: Task not found
        InvalidStateError: Task status not retryable or upstream tasks incomplete
    """
    stmt = select(TaskRecord).where(TaskRecord.task_id == task_id)
    result = await session.execute(stmt)
    task_record = result.scalar_one_or_none()

    if not task_record:
        msg = f'Task with task_id {task_id} not found'
        raise NotFoundError(msg)

    if task_record.status not in RETRYABLE_STATUSES:
        msg = (
            f'Task with status {task_record.status} cannot be retried. '
            f'Only FAILED, CANCELLED, and RETRY tasks can be retried.'
        )
        raise InvalidStateError(msg)

    if task_record.upstream_task_ids:
        upstream_stmt = select(TaskRecord).where(
            TaskRecord.task_id.in_(task_record.upstream_task_ids)
        )
        upstream_result = await session.execute(upstream_stmt)
        upstream_tasks = upstream_result.scalars().all()

        incomplete_upstream = [
            task for task in upstream_tasks if task.status != TaskStatus.COMPLETE
        ]

        if incomplete_upstream:
            incomplete_ids = [task.task_id for task in incomplete_upstream]
            msg = (
                f'Cannot retry task: upstream tasks {incomplete_ids} are not complete. '
                f'Please retry the upstream tasks first or wait for them to complete.'
            )
            raise InvalidStateError(msg)

    return task_record


async def _serialize_retry_data(session, task_record: TaskRecord, overwrite: bool) -> dict:
    """Retrieve and serialize the data needed to dispatch a retry task.

    Dispatches to _fetch_download_job_serialized or _fetch_transcript_task_serialized
    based on task_type, then applies overwrite flags.

    Must receive the caller's session because the TaskRecord is attached to it.

    Args:
        session: Active async database session
        task_record: The TaskRecord being retried
        overwrite: If True, set overwrite/force_recompute in the serialized data

    Returns:
        Serialized dict ready for dispatch

    Raises:
        InvalidStateError: Unsupported task type
        NotFoundError: Missing DownloadJob or MediaDetails
    """
    if task_record.task_type == TaskType.DOWNLOAD:
        serialized_data = await _fetch_download_job_serialized(
            session, task_record.id, f'download task {task_record.task_id}'
        )
        if overwrite:
            serialized_data['overwrite'] = True

    elif task_record.task_type == TaskType.TRANSCRIPT_GENERATION:
        serialized_data = await _fetch_transcript_task_serialized(
            session, task_record.id, f'transcript task {task_record.task_id}'
        )
        if overwrite:
            serialized_data['force_recompute'] = True

    elif task_record.task_type == TaskType.SPRITE_GENERATION:
        serialized_data = await _fetch_sprites_task_serialized(session, task_record)
    else:
        msg = f'Task type {task_record.task_type} is not supported for retry'
        raise InvalidStateError(msg)

    return serialized_data


async def _prepare_downstream_transcript_for_chain(new_task_id: str) -> str | None:
    """Find and prepare a downstream transcript task for chaining with a retried download.

    Opens its own session (matching the pattern for steps outside the main session block).
    Looks for a transcript task referencing new_task_id with UPSTREAM_FAILED or CANCELLED
    status. If found, assigns a new UUID and resets it for re-dispatch.

    Args:
        new_task_id: The new download task ID (upstream_task_ids already updated)

    Returns:
        The new transcript task ID, or None if no downstream transcript found
    """
    async with db.get_async_session() as session:
        stmt = select(TaskRecord).where(
            and_(
                text('task_records.upstream_task_ids::jsonb ? :new_task_id'),
                TaskRecord.status.in_(DOWNSTREAM_RETRYABLE_STATUSES),
                TaskRecord.task_type == TaskType.TRANSCRIPT_GENERATION,
            )
        )
        result = await session.execute(stmt, {'new_task_id': new_task_id})
        downstream_transcript_task = result.scalar_one_or_none()

        if not downstream_transcript_task:
            return None

        logger.info(
            f'Found downstream transcript task {downstream_transcript_task.task_id} to chain'
        )

        new_transcript_task_id = str(uuid.uuid4())
        downstream_transcript_task.task_id = new_transcript_task_id
        _reset_task_for_requeue(
            downstream_transcript_task, 'Queued - waiting for download to complete'
        )

        await session.commit()
        logger.info(f'Updated downstream transcript task: new_task_id={new_transcript_task_id}')
        return new_transcript_task_id


async def _prepare_downstream_sprite_for_chain(new_task_id: str) -> None:
    """Re-arm the sprite row behind a retried download.

    Unlike the transcript it is not a JobSpec downstream, so nothing is dispatched
    here — clearing queue_sequence is what re-arms it, since that null is the marker
    DownloadHooks.on_success reads as "this chain row hasn't been dispatched yet".

    Must run before _retry_other_downstream_tasks: that sweep has no task-type filter,
    and would otherwise recurse into this row and fail its upstream-complete check.
    """
    async with db.get_async_session() as session:
        stmt = select(TaskRecord).where(
            and_(
                text('task_records.upstream_task_ids::jsonb ? :new_task_id'),
                TaskRecord.status.in_(DOWNSTREAM_RETRYABLE_STATUSES),
                TaskRecord.task_type == TaskType.SPRITE_GENERATION,
            )
        )
        result = await session.execute(stmt, {'new_task_id': new_task_id})
        downstream_sprite_task = result.scalar_one_or_none()

        if not downstream_sprite_task:
            return

        _reset_task_for_requeue(downstream_sprite_task, 'Waiting for download to finish...')
        downstream_sprite_task.queue_sequence = None

        await session.commit()
        logger.info(f'Re-armed downstream sprite task {downstream_sprite_task.task_id}')


def _dispatch_retry_task(
    task_record: TaskRecord,
    serialized_data: dict,
    new_task_id: str,
    new_transcript_task_id: str | None,
):
    """Dispatch a retry task to the orchestrator.

    Routes to dispatch_download_chain or _dispatch_transcript_task based on task_type.

    Args:
        task_record: The TaskRecord being retried (used for task_type, user_id, priority)
        serialized_data: Serialized job payload
        new_task_id: The new task ID for the retried task
        new_transcript_task_id: Optional chained transcript task ID (downloads only)
    """
    if task_record.task_type == TaskType.DOWNLOAD:
        priority = (
            DIRECT_DOWNLOAD_PRIORITY
            if serialized_data.get('subscription_id') is None
            else SUBSCRIPTION_DOWNLOAD_PRIORITY
        )
        dispatch_download_chain(
            download_data=serialized_data,
            download_task_id=new_task_id,
            transcript_task_id=new_transcript_task_id,
            priority=priority,
            download_status_msg='Retrying task',
            transcript_status_msg='Queued - waiting for download',
            user_id=task_record.user_id,
        )
    elif task_record.task_type == TaskType.TRANSCRIPT_GENERATION:
        _dispatch_transcript_task(
            serialized_data,
            new_task_id,
            'Retrying task',
            priority=task_record.priority or 5,
            user_id=task_record.user_id,
        )
    elif task_record.task_type == TaskType.SPRITE_GENERATION:
        _dispatch_sprites_task(
            serialized_data,
            new_task_id,
            'Retrying task',
            priority=task_record.priority or 5,
            user_id=task_record.user_id,
        )


async def _retry_other_downstream_tasks(
    new_task_id: str, exclude_task_id: str | None
) -> tuple[int, list[str]]:
    """Find and retry downstream tasks that aren't the chained transcript.

    Opens its own session. Finds downstream tasks referencing new_task_id
    with UPSTREAM_FAILED or CANCELLED status, excluding exclude_task_id.
    Recursively calls retry_task_and_downstream with retry_downstream=False
    for each, preventing infinite recursion.

    Args:
        new_task_id: The new upstream task ID to search for in upstream_task_ids
        exclude_task_id: Task ID to skip (already handled as chained transcript)

    Returns:
        (count, [task_ids]) — total retried count and list of new task IDs
    """
    async with db.get_async_session() as session:
        stmt = select(TaskRecord).where(
            and_(
                text('task_records.upstream_task_ids::jsonb ? :new_task_id'),
                TaskRecord.status.in_(DOWNSTREAM_RETRYABLE_STATUSES),
            )
        )
        result = await session.execute(stmt, {'new_task_id': new_task_id})
        downstream_tasks = result.scalars().all()

        other_downstream_tasks = [dt for dt in downstream_tasks if dt.task_id != exclude_task_id]

        logger.info(f'Found {len(other_downstream_tasks)} other downstream tasks to retry')

    total_retried = 0
    retried_task_ids = []

    for dt in other_downstream_tasks:
        try:
            downstream_result = await retry_task_and_downstream(
                dt.task_id,
                False,  # Don't recursively retry downstream to avoid infinite loops
            )
            total_retried += downstream_result['retried_count']
            retried_task_ids.extend(downstream_result['task_ids'])
        except Exception:
            logger.exception(f'Failed to retry downstream task {dt.task_id}')

    return total_retried, retried_task_ids


def _transcript_only_unstaged(task_record: TaskRecord, serialized_data: dict) -> bool:
    """A transcript-only item's transcript, with nothing staged left for it to read.

    TranscriptHooks deletes the staged captions or audio when the transcript fails or is
    cancelled, so retrying the transcript job alone could only fail again.
    """
    if task_record.task_type != TaskType.TRANSCRIPT_GENERATION:
        return False
    url = serialized_data.get('url')
    if serialized_data.get('media_type') != MediaType.TRANSCRIPT or not url:
        return False
    return not (find_staged_audio(url) or find_subtitle_file(transcript_staging_base(url)))


async def _retry_transcript_only_from_download(session, transcript_record: TaskRecord) -> dict:
    """Retry a transcript-only item by re-running its download, its transcript chained behind.

    The download row is COMPLETE (it staged what it was asked to), which the ordinary retry
    refuses, so it is re-armed here directly. Both rows get fresh task ids, as any retry does.
    Commits the caller's session.
    """
    download_stmt = select(TaskRecord).where(
        and_(
            TaskRecord.task_id.in_(transcript_record.upstream_task_ids or []),
            TaskRecord.task_type == TaskType.DOWNLOAD,
        )
    )
    download_record = (await session.execute(download_stmt)).scalar_one_or_none()
    if download_record is None:
        msg = 'This transcript-only item has no download to re-run; request the URL again.'
        raise InvalidStateError(msg)

    download_data = await _fetch_download_job_serialized(
        session, download_record.id, f'download task {download_record.task_id}'
    )
    _, new_download_id = await _cancel_and_reassign_task_id(download_record)
    _reset_task_for_requeue(download_record, 'Retrying task')
    _, new_transcript_id = await _cancel_and_reassign_task_id(transcript_record)
    _reset_task_for_requeue(transcript_record, 'Queued - waiting for download')
    transcript_record.upstream_task_ids = [new_download_id]
    await session.commit()

    priority = (
        DIRECT_DOWNLOAD_PRIORITY
        if download_data.get('subscription_id') is None
        else SUBSCRIPTION_DOWNLOAD_PRIORITY
    )
    await asyncio.to_thread(
        dispatch_download_chain,
        download_data=download_data,
        download_task_id=new_download_id,
        transcript_task_id=new_transcript_id,
        priority=priority,
        download_status_msg='Retrying task',
        transcript_status_msg='Queued - waiting for download',
        user_id=download_record.user_id,
    )
    logger.info(
        f'Re-staging transcript-only item: download {new_download_id}, '
        f'transcript {new_transcript_id}'
    )
    return {'retried_count': 2, 'task_ids': [new_download_id, new_transcript_id]}


async def retry_task_and_downstream_by_id(
    record_id: int, retry_downstream: bool, overwrite: bool = False
) -> dict:
    """Retry a failed/cancelled task by database ID.

    This is a wrapper that looks up the task by its database primary key (id)
    instead of the task_id, which can change during retries.

    Args:
        record_id: The database primary key ID (never changes)
        retry_downstream: If True, also retry downstream tasks with UPSTREAM_FAILED status
        overwrite: If True and task is a download, force overwrite of existing files (hard retry)

    Returns:
        dict with 'retried_count' (int) and 'task_ids' (list[str])

    Raises:
        NotFoundError / InvalidStateError: If task not found or not in a retryable status
    """
    task_record = await get_task_by_id(record_id)
    if not task_record:
        msg = f'Task with id {record_id} not found'
        raise NotFoundError(msg)

    return await retry_task_and_downstream(task_record.task_id, retry_downstream, overwrite)


async def retry_task_and_downstream(
    task_id: str, retry_downstream: bool, overwrite: bool = False
) -> dict:
    """Retry a failed/cancelled task under a fresh task_id.

    Optionally retry downstream tasks that have UPSTREAM_FAILED status.

    Args:
        task_id: The task ID to retry
        retry_downstream: If True, also retry downstream tasks with UPSTREAM_FAILED status
        overwrite: If True and task is a download, force overwrite of existing files (hard retry)

    Returns:
        dict with 'retried_count' (int) and 'task_ids' (list[str])

    Raises:
        NotFoundError / InvalidStateError: If task not found or not in a retryable status
    """
    # Validate, serialize, cancel the old job, and reset — all within a single session.
    async with db.get_async_session() as session:
        task_record = await _validate_task_for_retry(session, task_id)
        serialized_data = await _serialize_retry_data(session, task_record, overwrite)
        if _transcript_only_unstaged(task_record, serialized_data):
            return await _retry_transcript_only_from_download(session, task_record)
        old_task_id, new_task_id = await _cancel_and_reassign_task_id(task_record)
        _reset_task_for_requeue(task_record, 'Retrying task')
        await session.commit()

    logger.info(
        f'Updated TaskRecord for retry: old_task_id={old_task_id}, new_task_id={new_task_id}'
    )

    await _replace_upstream_task_ids(old_task_id, new_task_id)

    new_transcript_task_id = None
    if retry_downstream and task_record.task_type == TaskType.DOWNLOAD:
        new_transcript_task_id = await _prepare_downstream_transcript_for_chain(new_task_id)
        await _prepare_downstream_sprite_for_chain(new_task_id)

    # Sync DB writes inside — keep them off the event loop.
    await asyncio.to_thread(
        _dispatch_retry_task, task_record, serialized_data, new_task_id, new_transcript_task_id
    )

    retried_task_ids = [new_task_id]
    total_retried = 1
    if new_transcript_task_id:
        retried_task_ids.append(new_transcript_task_id)
        total_retried += 1

    if retry_downstream:
        count, ids = await _retry_other_downstream_tasks(new_task_id, new_transcript_task_id)
        total_retried += count
        retried_task_ids.extend(ids)

    return {'retried_count': total_retried, 'task_ids': retried_task_ids}


async def prioritize_task(record_id: int) -> dict:
    """Prioritize a QUEUED download task so it executes next.

    Reorders the job to the front of the downloads lane (priority 0,
    queue_sequence 0). The task_id never changes, so downstream
    upstream_task_ids stay valid.

    Args:
        record_id: The TaskRecord database primary key

    Returns:
        dict with 'status', 'record_id', and 'new_task_id' (unchanged task_id,
        field name kept for API compatibility)

    Raises:
        NotFoundError / InvalidStateError: If task not found, not QUEUED, or not a DOWNLOAD task
    """
    from orchestrator import orch

    async with db.get_async_session() as session:
        stmt = select(TaskRecord).where(TaskRecord.id == record_id)
        result = await session.execute(stmt)
        task_record = result.scalar_one_or_none()

        if not task_record:
            msg = f'Task with id {record_id} not found'
            raise NotFoundError(msg)

        if task_record.status != TaskStatus.QUEUED:
            msg = f'Only QUEUED tasks can be prioritized. Current status: {task_record.status}'
            raise InvalidStateError(msg)

        if task_record.task_type != TaskType.DOWNLOAD:
            msg = f'Only DOWNLOAD tasks can be prioritized. Current type: {task_record.task_type}'
            raise InvalidStateError(msg)

        task_record.queue_sequence = 0
        task_record.priority = 0
        task_record.status_message = 'Prioritized'
        await session.commit()

    # Reorder the in-memory queue entry (the DB fields above are the persisted ordering)
    moved = await orch.prioritize(task_record.task_id)
    if not moved:
        # Not in the queue (already running, or dispatched between fetch and
        # here) — it is effectively at the front already; the DB ordering
        # above keeps the UI consistent.
        logger.info(
            f'Prioritize: task {task_record.task_id} not queued in orchestrator '
            f'(already running or dispatched)'
        )

    publish_status_change(
        task_record.task_id,
        TaskStatus.QUEUED.value,
        'Prioritized',
        user_id=task_record.user_id,
    )

    return {'status': 'prioritized', 'record_id': record_id, 'new_task_id': task_record.task_id}
