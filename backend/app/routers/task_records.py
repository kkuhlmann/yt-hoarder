import asyncio
from functools import partial
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from dependencies import (
    get_admin_user_id,
    get_effective_user_id,
    get_entity_or_404,
    get_required_user_id,
)
from models import MediaType, TaskStatus, TaskType
from orchestrator import orch
from repositories import media_details as md_repo
from repositories import settings as settings_repo
from repositories import task_records as tr_repo
from repositories.errors import InvalidStateError, NotFoundError
from schemas import (
    BulkCancelRequest,
    BulkDeleteRequest,
    BulkRetryRequest,
    RetryTaskRequest,
    TaskStats,
)
from services.cleanup import cleanup_task_files
from ytdlp.files import delete_transcript_staging

router = APIRouter()


async def _check_task_owner_or_raise(task_record, user_id: int, is_admin: bool = False):
    """Check if a user owns a task. Admins bypass the check."""
    if is_admin:
        return
    if task_record.user_id == user_id:
        return
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f'Task with id {task_record.id} not found',
    )


@router.get(
    '/stats',
    status_code=status.HTTP_200_OK,
    response_description='Task queue statistics',
    response_model=TaskStats,
)
async def get_task_stats(
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get task queue statistics.

    Returns counts for:
    - processing: tasks with IN_PROGRESS or POSTPROCESSING status
    - queued_total: total queued tasks
    - queued_downloads: queued download tasks
    - queued_transcripts: queued transcript generation tasks
    - retry: tasks with RETRY status
    - failed: tasks with FAILED status
    - not_ready: unreleased videos (live / premiere / post-live) awaiting release
    - completed_24h: tasks completed in the last 24 hours
    """
    stats = await tr_repo.get_task_stats(user_id=effective_user_id)
    return TaskStats(**stats)


@router.get(
    '',
    status_code=status.HTTP_200_OK,
    response_description='Task records matching criteria',
    response_model=dict[str, int | list[dict[str, Any]]],
)
async def get_all_task_records(
    statuses: str | None = None,
    since_hours: int = 24,
    page: int = 1,
    page_size: int | None = None,
    sort_by: str | None = None,
    sort_direction: str = 'desc',
    search: str | None = None,
    effective_user_id: int | None = Depends(get_effective_user_id),
):
    """Get task records with optional filtering.

    Query params:
        statuses: Comma-separated list of statuses to filter by (e.g., "IN_PROGRESS,QUEUED,COMPLETE")
        since_hours: For COMPLETE status, only include tasks within this timeframe (default: 24)
        page: Page number (default: 1)
        page_size: Records per page (reads from settings if not provided)
        sort_by: Field to sort by (e.g., "created_at")
        sort_direction: Sort direction - "asc" or "desc" (default: "desc")
    """
    if page_size is None:
        settings = await settings_repo.get_settings()
        page_size = settings.download_table_page_size
    status_list = statuses.split(',') if statuses else None
    return await tr_repo.get_filtered_tasks(
        statuses=status_list,
        since_hours=since_hours,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_direction=sort_direction,
        search=search,
        user_id=effective_user_id,
    )


# --- Bulk operations (must come before /{task_id} routes to avoid route conflicts) ---


# Statuses a task can still be cancelled out of. Mirrors CANCELLABLE_TASK_STATUSES in
# frontend/app/lib/taskStatus.ts, which decides when the button is offered.
_CANCELLABLE_TASK_STATUSES = (
    TaskStatus.RESOLVING,
    TaskStatus.QUEUED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.POSTPROCESSING,
    TaskStatus.RETRY,
    TaskStatus.NOT_READY,
)


async def _cancel_task_ids(task_ids: list[str]) -> dict:  # noqa: C901 — per-task status matrix; each branch is a distinct cancellable state
    """Stop these jobs, clean up what they left behind, and write CANCELLED.

    Shared by the cancel endpoint and the delete one, which has to cancel before it can
    retire a row: deleting a still-active task otherwise hides it while its job keeps
    running and keeps holding the URL.
    """
    if not task_ids:
        return {
            'cancelled_count': 0,
            'downstream_cancelled': 0,
            'files_deleted': 0,
            'transcript_blocks_deleted': 0,
            'errors': [],
        }

    # 1. Cancel every job in the orchestrator (dequeue queued ones;
    #    signal/terminate running ones)
    for task_id in task_ids:
        await orch.cancel(task_id)

    # 2. Batch fetch all task records in a single DB query
    task_records = await tr_repo.get_tasks_by_task_ids(task_ids)

    # 3. Prepare concurrent cleanup tasks
    async def cleanup_download(task_record):
        """Cleanup download files (runs in thread pool since it's sync I/O)."""
        # A RESOLVING row's title is the raw submitted URL and its download was never
        # dispatched — same guard as revoke_task; there are no partials to sweep.
        if task_record.title and task_record.status != TaskStatus.RESOLVING:
            return cleanup_task_files(
                task_title=task_record.title, task_url=task_record.download_job_url
            )
        return 0

    async def cleanup_transcript(task_record):
        """Cleanup transcript blocks."""
        from repositories import transcript_blocks as tb_repo

        media_details = await md_repo.get_media_details_by_transcript_task_record_id(task_record.id)
        if media_details:
            return await tb_repo.delete_transcript_block_by_media_details_id(media_details.id)
        return 0

    # Build list of cleanup coroutines based on task type
    cleanup_tasks = []
    cleanup_types = []  # Track which type each cleanup is for aggregation

    for task_record in task_records:
        if task_record.task_type == TaskType.DOWNLOAD:
            cleanup_tasks.append(cleanup_download(task_record))
            cleanup_types.append('download')
        elif task_record.task_type == TaskType.TRANSCRIPT_GENERATION:
            cleanup_tasks.append(cleanup_transcript(task_record))
            cleanup_types.append('transcript')

    # 4. Run all cleanup tasks concurrently
    cleanup_results = await asyncio.gather(*cleanup_tasks, return_exceptions=True)

    # Aggregate cleanup results
    files_deleted = 0
    transcript_blocks_deleted = 0
    for i, result in enumerate(cleanup_results):
        if isinstance(result, Exception):
            continue  # Skip failed cleanups
        if cleanup_types[i] == 'download':
            files_deleted += result
        else:
            transcript_blocks_deleted += result

    # 5. Batch update database records (already efficient)
    result = await tr_repo.bulk_cancel_tasks(task_ids)

    return {
        'cancelled_count': result['cancelled_count'],
        'downstream_cancelled': result['downstream_cancelled'],
        'files_deleted': files_deleted,
        'transcript_blocks_deleted': transcript_blocks_deleted,
        'errors': result['errors'],
    }


async def _owned_task_ids(request: Request, task_ids: list[str], user_id: int) -> list[str]:
    """Task IDs the caller may act on. Admins act on all; others' IDs are dropped."""
    if request.state.is_admin:
        return task_ids
    task_records = await tr_repo.get_tasks_by_task_ids(task_ids)
    return [tr.task_id for tr in task_records if tr.user_id == user_id]


@router.post(
    '/bulk/cancel',
    status_code=status.HTTP_200_OK,
    response_description='Cancel multiple tasks and their downstream tasks',
    response_model=dict,
)
async def bulk_cancel_tasks(
    request: Request, bulk_req: BulkCancelRequest, user_id: int = Depends(get_required_user_id)
):
    """Cancel multiple tasks and mark their downstream tasks as cancelled.

    Non-admin users can only cancel their own tasks. Task IDs not belonging to the
    user are silently filtered out.
    """
    return await _cancel_task_ids(await _owned_task_ids(request, bulk_req.task_ids, user_id))


@router.delete(
    '/bulk',
    status_code=status.HTTP_200_OK,
    response_description='Cancel any still-running tasks, then remove the records',
    response_model=dict,
)
async def bulk_delete_tasks(
    request: Request, bulk_req: BulkDeleteRequest, user_id: int = Depends(get_required_user_id)
):
    """Remove multiple task records, cancelling any that are still running.

    Deleting a task means the work is abandoned: an unfinished job is stopped and its
    partial files cleaned up first, so nothing keeps running behind a record the user
    can no longer see. Non-admin users can only delete their own tasks.
    """
    records = await tr_repo.get_tasks_by_ids(bulk_req.record_ids)
    if not request.state.is_admin:
        records = [tr for tr in records if tr.user_id == user_id]

    await _cancel_task_ids(
        [tr.task_id for tr in records if tr.status in _CANCELLABLE_TASK_STATUSES]
    )

    return await tr_repo.bulk_delete_tasks([tr.id for tr in records])


@router.post(
    '/bulk/retry',
    status_code=status.HTTP_200_OK,
    response_description='Retry multiple failed or cancelled tasks',
    response_model=dict,
)
async def bulk_retry_tasks(
    request: Request, bulk_req: BulkRetryRequest, user_id: int = Depends(get_required_user_id)
):
    """Retry multiple failed or cancelled tasks.

    Non-admin users can only retry their own tasks.
    """
    record_ids = bulk_req.record_ids

    if not request.state.is_admin:
        filtered_ids = []
        for record_id in record_ids:
            task = await tr_repo.get_task_by_id(record_id)
            if task and task.user_id == user_id:
                filtered_ids.append(record_id)
        record_ids = filtered_ids

    return await tr_repo.bulk_retry_tasks(record_ids, bulk_req.retry_downstream, bulk_req.overwrite)


# --- Single task operations ---


@router.get(
    '/runtime',
    status_code=status.HTTP_200_OK,
    response_description='Orchestrator runtime snapshot (lanes, queued and running jobs)',
    response_model=dict,
)
async def get_runtime_snapshot(_user_id: int = Depends(get_admin_user_id)):
    """Live orchestrator state for admins: lanes, queued and running jobs."""
    return orch.runtime_snapshot()


@router.get(
    '/{task_id}',
    status_code=status.HTTP_200_OK,
    response_description='Status of a background job',
    response_model=dict,
)
async def get_task_status(
    request: Request,
    task_id: str,
    user_id: int = Depends(get_required_user_id),
):
    """Job state string (PENDING/STARTED/SUCCESS/FAILURE/RETRY/REVOKED).

    The frontend polls this for jobs without TaskRecords (add-subscription).
    """
    is_admin = request.state.is_admin

    task_record = await tr_repo.get_task_by_task_id(task_id)
    if task_record is not None:
        await _check_task_owner_or_raise(task_record, user_id, is_admin)
    elif not is_admin:
        # Untracked jobs (add-subscription) exist only in the orchestrator's
        # registry. An id that is genuinely unknown falls through and reports
        # PENDING, which is what it reports for everyone — so probing an
        # arbitrary id reveals nothing either way.
        known, owner_id = orch.get_result_registry_owner(task_id)
        if known and owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f'Task with id {task_id} not found',
            )

    return {'status': await orch.get_status(task_id)}


@router.delete(
    '/{task_id}',
    status_code=status.HTTP_200_OK,
    response_description='Cancels a job and marks downstream as cancelled',
    response_model=dict,
)
async def revoke_task(
    request: Request,
    task_id: str,
    user_id: int = Depends(get_required_user_id),
):
    """Cancel a task, update TaskRecord status, and mark downstream tasks."""
    # Get task record before updating (to check type, ownership, and get title)
    task_record = await get_entity_or_404(
        tr_repo.get_task_by_task_id,
        task_id,
        'Task',
        access_check=partial(
            _check_task_owner_or_raise, user_id=user_id, is_admin=request.state.is_admin
        ),
    )

    # Cancel in the orchestrator: dequeues a queued job immediately; a running
    # download aborts at its next progress tick via the cancel event.
    await orch.cancel(task_id)

    # Update the TaskRecord status in the database
    try:
        await tr_repo.update_one(
            task_id, {'status': TaskStatus.CANCELLED, 'status_message': 'Cancelled by user'}
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Task with task_id {task_id} not found',
        ) from e

    # Clean up partial download files if this is a download task
    files_deleted = 0
    transcript_blocks_deleted = 0

    if task_record.task_type == TaskType.DOWNLOAD and task_record.download_job_url:
        # Terminal status for the media row: a queued-then-cancelled download never
        # ran a hook, so without this it keeps populate's NONE and every later
        # subscription tick re-includes the URL forever.
        await md_repo.mark_download_cancelled(task_record.download_job_url, task_record.media_type)

    if (
        task_record.task_type == TaskType.DOWNLOAD
        and task_record.title
        # A RESOLVING row's title is the raw submitted URL, and cleanup globs
        # *{title}*.part across both media dirs — meaningless work for a task whose
        # download was never dispatched.
        and task_record.status != TaskStatus.RESOLVING
    ):
        files_deleted = cleanup_task_files(
            task_title=task_record.title, task_url=task_record.download_job_url
        )
    elif task_record.task_type == TaskType.TRANSCRIPT_GENERATION:
        # Clean up partial transcript blocks for cancelled transcript tasks
        from repositories import transcript_blocks as tb_repo

        # Find the MediaDetails associated with this transcript task
        media_details = await md_repo.get_media_details_by_transcript_task_record_id(task_record.id)
        if media_details:
            # Delete any partial transcript blocks
            transcript_blocks_deleted = await tb_repo.delete_transcript_block_by_media_details_id(
                media_details.id
            )

    if task_record.media_type == MediaType.TRANSCRIPT and task_record.download_job_url:
        # A transcript-only item's transcript job may never have started (cancelled while
        # queued), and then no hook is left to delete what its download staged or to end
        # the item, which would sit in POSTPROCESSING forever.
        files_deleted += delete_transcript_staging(task_record.download_job_url)
        await md_repo.finish_transcript_only(task_record.download_job_url, TaskStatus.CANCELLED)

    # Mark downstream tasks as cancelled
    downstream_count = await tr_repo.mark_downstream_as_cancelled(task_id)

    return {
        'status': 'cancelled',
        'task_id': task_id,
        'downstream_tasks_cancelled': downstream_count,
        'files_deleted': files_deleted,
        'transcript_blocks_deleted': transcript_blocks_deleted,
    }


@router.post(
    '/{record_id}/prioritize',
    status_code=status.HTTP_200_OK,
    response_description='Prioritize a queued download task to execute next',
    response_model=dict,
)
async def prioritize_task(
    request: Request, record_id: int, user_id: int = Depends(get_required_user_id)
):
    """Prioritize a QUEUED download task so it executes next."""
    # Return value unused; called for the 404 and the ownership check.
    await get_entity_or_404(
        tr_repo.get_task_by_id,
        record_id,
        'Task',
        access_check=partial(
            _check_task_owner_or_raise, user_id=user_id, is_admin=request.state.is_admin
        ),
    )

    try:
        return await tr_repo.prioritize_task(record_id)
    except (HTTPException, NotFoundError, InvalidStateError):
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f'Failed to prioritize task: {e!s}',
        ) from e


@router.post(
    '/{record_id}/retry',
    status_code=status.HTTP_200_OK,
    response_description='Retry a failed or cancelled task and optionally retry downstream tasks',
    response_model=dict,
)
async def retry_task(
    request: Request,
    record_id: int,
    retry_req: RetryTaskRequest = Body(default_factory=RetryTaskRequest),
    user_id: int = Depends(get_required_user_id),
):
    """Retry a failed or cancelled task."""
    # Return value unused; called for the 404 and the ownership check.
    await get_entity_or_404(
        tr_repo.get_task_by_id,
        record_id,
        'Task',
        access_check=partial(
            _check_task_owner_or_raise, user_id=user_id, is_admin=request.state.is_admin
        ),
    )

    try:
        result = await tr_repo.retry_task_and_downstream_by_id(
            record_id, retry_req.retry_downstream, retry_req.overwrite
        )
        return {
            'status': 'retrying',
            'record_id': record_id,
            'retried_count': result['retried_count'],
            'task_ids': result['task_ids'],
        }
    except (HTTPException, NotFoundError, InvalidStateError):
        # Repository errors carry their own status; only unexpected failures become 500s.
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f'Failed to retry task: {e!s}',
        ) from e
