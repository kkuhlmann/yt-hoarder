# --- Bulk operations ---


from sqlalchemy import case, select, update

from database import db
from logger import logger
from models import MediaType, TaskRecord, TaskStatus, TaskType, utc_now
from repositories import media_details as md_repo
from ytdlp.files import delete_transcript_staging

from ..errors import InvalidStateError, NotFoundError
from .crud import SLOT_HOLDING_STATUSES
from .retry import mark_downstream_as_cancelled, retry_task_and_downstream_by_id


async def bulk_cancel_tasks(task_ids: list[str]) -> dict:
    """Cancel multiple tasks and cascade to their downstream tasks.

    Args:
        task_ids: List of task IDs to cancel

    Returns:
        dict with 'cancelled_count', 'downstream_cancelled', and 'errors'
    """
    async with db.get_async_session() as session:
        stmt = (
            update(TaskRecord)
            .where(TaskRecord.task_id.in_(task_ids))
            .values(
                status=TaskStatus.CANCELLED,
                status_message='Cancelled by user (bulk)',
                updated_at=utc_now(),
            )
        )
        result = await session.execute(stmt)
        cancelled_count = result.rowcount

        # Downloads need their media row given a terminal status too — see
        # mark_download_cancelled for what goes wrong when they don't get one.
        downloads = (
            await session.execute(
                select(TaskRecord.download_job_url, TaskRecord.media_type).where(
                    TaskRecord.task_id.in_(task_ids),
                    TaskRecord.task_type == TaskType.DOWNLOAD,
                    TaskRecord.download_job_url.isnot(None),
                )
            )
        ).all()
        # Either half of a transcript-only chain: see revoke_task for why the item is
        # ended here rather than left to a hook that may never run.
        transcript_only_urls = (
            (
                await session.execute(
                    select(TaskRecord.download_job_url)
                    .where(
                        TaskRecord.task_id.in_(task_ids),
                        TaskRecord.media_type == MediaType.TRANSCRIPT,
                        TaskRecord.download_job_url.isnot(None),
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        await session.commit()

    for url, media_type in downloads:
        await md_repo.mark_download_cancelled(url, media_type)
    for url in transcript_only_urls:
        delete_transcript_staging(url)
        await md_repo.finish_transcript_only(url, TaskStatus.CANCELLED)

    downstream_cancelled = 0
    errors = []
    for task_id in task_ids:
        try:
            downstream_cancelled += await mark_downstream_as_cancelled(task_id)
        except Exception as e:  # noqa: BLE001 — one bad item must not abort the batch
            errors.append({'task_id': task_id, 'error': str(e)})

    return {
        'cancelled_count': cancelled_count,
        'downstream_cancelled': downstream_cancelled,
        'errors': errors,
    }


async def bulk_delete_tasks(record_ids: list[int]) -> dict:
    """Soft delete task records, releasing any URL slot they still hold.

    deleted_at alone is not enough: ix_task_records_active_unique's predicate has no
    deleted_at clause, so a deleted row in a slot-holding status keeps blocking its URL
    against a task the user can no longer see, retry or delete. Moving the status out of
    the predicate is what frees it. Terminal statuses keep theirs — they never held the
    slot, and the status is the record of what happened.

    Args:
        record_ids: List of database primary key IDs to delete

    Returns:
        dict with 'deleted_count' and 'errors'
    """
    async with db.get_async_session() as session:
        stmt = (
            update(TaskRecord)
            .where(TaskRecord.id.in_(record_ids))
            .values(
                status=case(
                    (TaskRecord.status.in_(SLOT_HOLDING_STATUSES), TaskStatus.DELETED),
                    else_=TaskRecord.status,
                ),
                deleted_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        result = await session.execute(stmt)
        deleted_count = result.rowcount
        await session.commit()
        logger.info(f'Soft deleted {deleted_count} tasks')
        return {'deleted_count': deleted_count, 'errors': []}


async def bulk_retry_tasks(record_ids: list[int], retry_downstream: bool, overwrite: bool) -> dict:
    """Retry multiple failed/cancelled tasks.

    Args:
        record_ids: List of database primary key IDs to retry
        retry_downstream: If True, also retry downstream tasks
        overwrite: If True and task is download, force overwrite (hard retry)

    Returns:
        dict with 'retried_count', 'task_ids', and 'errors'
    """
    retried_count = 0
    task_ids = []
    errors = []

    for record_id in record_ids:
        try:
            result = await retry_task_and_downstream_by_id(record_id, retry_downstream, overwrite)
            retried_count += result['retried_count']
            task_ids.extend(result['task_ids'])
        except (NotFoundError, InvalidStateError) as e:
            errors.append({'record_id': record_id, 'error': str(e)})
        except Exception as e:  # noqa: BLE001 — one bad item must not abort the batch
            errors.append({'record_id': record_id, 'error': str(e)})

    logger.info(f'Bulk retried {retried_count} tasks')
    return {'retried_count': retried_count, 'task_ids': task_ids, 'errors': errors}
