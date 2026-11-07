# --- Async functions for FastAPI ---

import bisect
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, and_, asc, bindparam, case, desc, func, or_, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlmodel import select

from database import db
from logger import logger
from models import TASK_QUEUE_SEQUENCE, TaskRecord, TaskStatus, TaskType, utc_now
from repositories.pagination import page_count

# Defined here (not imported from tasks/, which imports these from here)
# to avoid a circular import.
DIRECT_DOWNLOAD_PRIORITY = 1
SUBSCRIPTION_DOWNLOAD_PRIORITY = 5

# Mirrors ix_task_records_active_unique's predicate. A row in one of these holds its
# URL against every later insert, and the predicate is raw SQL text in models.py, so
# nothing but this comment keeps the two in step.
SLOT_HOLDING_STATUSES = (
    TaskStatus.RESOLVING,
    TaskStatus.QUEUED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.RETRY,
    TaskStatus.CANCELLED,
)

# Task types sharing the single serial `ml` lane. queue_position and the queued
# stats both group by lane, so a new ml job type must be listed here or its queue
# numbers get computed against the downloads queue.
ML_LANE_TASK_TYPES = (
    TaskType.TRANSCRIPT_GENERATION,
    TaskType.CLIP_GENERATION,
    TaskType.SPRITE_GENERATION,
)


PROGRESS_FEED_ACTIVE_STATUSES = (
    TaskStatus.RESOLVING,
    TaskStatus.QUEUED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.POSTPROCESSING,
    TaskStatus.RETRY,
    TaskStatus.NOT_READY,
)
PROGRESS_FEED_FINISHED_STATUSES = (TaskStatus.COMPLETE, TaskStatus.FAILED)


def _status_order():
    """Rank statuses for display: running, then resolving, queued, retrying, not ready, rest.

    RESOLVING outranks QUEUED: it has no queue_sequence, so under a queue-order tiebreak it
    would otherwise sort behind every dispatched row — putting a just-submitted download
    pages away from the user who submitted it.
    """
    return case(
        (TaskRecord.status == TaskStatus.IN_PROGRESS, 1),
        (TaskRecord.status == TaskStatus.POSTPROCESSING, 1),
        (TaskRecord.status == TaskStatus.RESOLVING, 2),
        (TaskRecord.status == TaskStatus.QUEUED, 3),
        (TaskRecord.status == TaskStatus.RETRY, 4),
        (TaskRecord.status == TaskStatus.NOT_READY, 5),
        else_=6,
    )


def _run_order() -> tuple:
    """`_status_order`, then the order the lanes pop in (priority, queue_sequence).

    So users see tasks in the order they'll execute.
    """
    return (
        _status_order(),
        asc(func.coalesce(TaskRecord.priority, 5)).nullslast(),
        asc(TaskRecord.queue_sequence).nullslast(),
        desc(TaskRecord.created_at),
    )


async def get_next_queue_sequence() -> int:
    """Async variant of sync_get_next_queue_sequence for router/dispatch paths."""
    async with db.get_async_session() as session:
        return await session.scalar(select(TASK_QUEUE_SEQUENCE.next_value()))


async def get_task_stats(user_id: int | None = None) -> dict:
    """Get task statistics grouped by status and task type.

    Args:
        user_id: When provided, only count tasks owned by this user.
                 When None, count all tasks (admin view).

    Returns:
        dict with queued_total, queued_downloads, queued_transcripts,
        processing, failed, retry, not_ready, completed_24h
    """
    async with db.get_async_session() as session:
        conditions = [TaskRecord.deleted_at.is_(None)]
        if user_id is not None:
            conditions.append(TaskRecord.user_id == user_id)

        cutoff = (datetime.now(UTC) - timedelta(hours=24)).replace(tzinfo=None)
        transcript_types = list(ML_LANE_TASK_TYPES)
        stmt = (
            select(
                func.count()
                .filter(TaskRecord.status.in_([TaskStatus.IN_PROGRESS, TaskStatus.POSTPROCESSING]))
                .label('processing'),
                # RESOLVING counts as queued so the stat tile matches the Queued filter
                # chip, which shows both.
                func.count()
                .filter(
                    TaskRecord.status.in_([TaskStatus.QUEUED, TaskStatus.RESOLVING]),
                    TaskRecord.task_type == TaskType.DOWNLOAD,
                )
                .label('queued_downloads'),
                func.count()
                .filter(
                    TaskRecord.status == TaskStatus.QUEUED,
                    TaskRecord.task_type.in_(transcript_types),
                )
                .label('queued_transcripts'),
                func.count().filter(TaskRecord.status == TaskStatus.RETRY).label('retry'),
                func.count().filter(TaskRecord.status == TaskStatus.FAILED).label('failed'),
                # NOT_READY = unreleased videos (live / premiere / post-live)
                func.count().filter(TaskRecord.status == TaskStatus.NOT_READY).label('not_ready'),
                func.count()
                .filter(TaskRecord.status == TaskStatus.COMPLETE, TaskRecord.updated_at >= cutoff)
                .label('completed_24h'),
            )
            .select_from(TaskRecord)
            .where(and_(*conditions))
        )
        row = (await session.execute(stmt)).one()

        return {
            'queued_total': row.queued_downloads + row.queued_transcripts,
            'queued_downloads': row.queued_downloads,
            'queued_transcripts': row.queued_transcripts,
            'processing': row.processing,
            'failed': row.failed,
            'retry': row.retry,
            'not_ready': row.not_ready,
            'completed_24h': row.completed_24h,
        }


async def insert_task(task: TaskRecord) -> TaskRecord:
    """Insert a new task record."""
    async with db.get_async_session() as session:
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


async def insert_many_tasks(tasks: list[TaskRecord]) -> list[int]:
    """Insert multiple task records and return their IDs."""
    async with db.get_async_session() as session:
        session.add_all(tasks)
        await session.commit()
        # IDs are populated on model instances after commit
        return [task.id for task in tasks]


async def update_one(task_id: str, updated_fields: dict) -> TaskRecord:
    """Update a task record by task_id."""
    async with db.get_async_session() as session:
        stmt = select(TaskRecord).where(TaskRecord.task_id == task_id)
        result = await session.execute(stmt)
        task = result.scalar_one_or_none()

        if not task:
            msg = f'Task with task_id: {task_id} was not found'
            raise ValueError(msg)

        # Copy to avoid mutating caller's dict
        updated_fields = {**updated_fields}
        updated_fields.setdefault('updated_at', utc_now())

        modified = False
        for key, value in updated_fields.items():
            if hasattr(task, key) and getattr(task, key) != value:
                setattr(task, key, value)
                modified = True

        if not modified:
            logger.debug(f'Task with task_id: {task_id} was not updated')

        await session.commit()
        await session.refresh(task)
        return task


async def get_task_by_task_id(task_id: str) -> TaskRecord | None:
    """Get a task by its task_id."""
    async with db.get_async_session() as session:
        stmt = select(TaskRecord).where(TaskRecord.task_id == task_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_task_by_id(id: int) -> TaskRecord | None:
    """Get a task by its primary key id."""
    async with db.get_async_session() as session:
        stmt = select(TaskRecord).where(TaskRecord.id == id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_tasks_by_ids(record_ids: list[int]) -> list[TaskRecord]:
    """Fetch multiple tasks by their primary keys in a single query.

    Args:
        record_ids: List of database primary key IDs to fetch

    Returns:
        List of TaskRecord objects (may be fewer than record_ids if some not found)
    """
    if not record_ids:
        return []
    async with db.get_async_session() as session:
        stmt = select(TaskRecord).where(TaskRecord.id.in_(record_ids))
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_tasks_by_task_ids(task_ids: list[str]) -> list[TaskRecord]:
    """Fetch multiple tasks by their task_ids in a single query.

    Args:
        task_ids: List of task IDs to fetch

    Returns:
        List of TaskRecord objects (may be fewer than task_ids if some not found)
    """
    if not task_ids:
        return []
    async with db.get_async_session() as session:
        stmt = select(TaskRecord).where(TaskRecord.task_id.in_(task_ids))
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_filtered_tasks(  # noqa: C901 — one branch per optional filter; splitting scatters rather than reduces
    statuses: list[str] | None = None,
    since_hours: int | None = None,
    page: int = 1,
    page_size: int = 25,
    sort_by: str | None = None,
    sort_direction: str = 'desc',
    search: str | None = None,
    user_id: int | None = None,
) -> dict[str, int | list[dict[str, Any]]]:
    """Get tasks filtered by status and/or time, with custom ordering.

    Args:
        statuses: List of TaskStatus values to include (e.g., ['IN_PROGRESS', 'QUEUED', 'COMPLETE'])
        since_hours: Only include COMPLETE tasks created within this many hours
        page: Page number for pagination
        page_size: Number of records per page
        sort_by: Field to sort by (e.g., "created_at")
        sort_direction: Sort direction - "asc" or "desc" (default: "desc")

    Returns:
        dict with 'count_records' and 'records' keys

    Ordering: IN_PROGRESS/POSTPROCESSING first, then QUEUED, then others by created_at desc (unless sort_by is provided)
    """
    async with db.get_async_session() as session:
        stmt = select(TaskRecord)
        count_stmt = select(func.count()).select_from(TaskRecord)

        conditions = [TaskRecord.deleted_at.is_(None)]

        if user_id is not None:
            conditions.append(TaskRecord.user_id == user_id)

        if statuses:
            status_conditions = []
            for status_str in statuses:
                try:
                    task_status = TaskStatus(status_str)
                    if task_status == TaskStatus.COMPLETE and since_hours:
                        # Strip timezone for PostgreSQL TIMESTAMP WITHOUT TIME ZONE column
                        cutoff = (datetime.now(UTC) - timedelta(hours=since_hours)).replace(
                            tzinfo=None
                        )
                        status_conditions.append(
                            and_(
                                TaskRecord.status == task_status,
                                TaskRecord.updated_at >= cutoff,
                            )
                        )
                    else:
                        status_conditions.append(TaskRecord.status == task_status)
                except ValueError:
                    continue

            if status_conditions:
                conditions.append(or_(*status_conditions))

        if search:
            conditions.append(
                or_(
                    TaskRecord.title.ilike(f'%{search}%'),
                    TaskRecord.channel.ilike(f'%{search}%'),
                )
            )

        if conditions:
            stmt = stmt.where(and_(*conditions))
            count_stmt = count_stmt.where(and_(*conditions))

        count_result = await session.execute(count_stmt)
        total_count = count_result.scalar() or 0

        if sort_by and hasattr(TaskRecord, sort_by):
            sort_column = getattr(TaskRecord, sort_by)
            logger.debug(f'Sorting tasks by {sort_by} {sort_direction}')
            if sort_direction == 'asc':
                # For ascending order, nulls first (so null values appear before non-null)
                stmt = stmt.order_by(asc(sort_column).nullsfirst(), TaskRecord.id.asc())
            else:
                # For descending order, nulls last (so non-null values appear first)
                stmt = stmt.order_by(desc(sort_column).nullslast(), TaskRecord.id.desc())
        else:
            logger.debug(
                f'Using default task ordering (sort_by={sort_by}, hasattr={hasattr(TaskRecord, sort_by) if sort_by else "N/A"})'
            )
            stmt = stmt.order_by(*_run_order())

        offset = (page - 1) * page_size
        stmt = stmt.offset(offset).limit(page_size)

        result = await session.execute(stmt)
        tasks = result.scalars().all()

        # Compute queue positions for QUEUED tasks on this page.
        # Groups tasks into "downloads" and "transcripts" queues (matching lane routing),
        # then for each queued task: position = (# processing in queue) + (# queued ahead).
        # Lanes pop by (priority ASC, queue_sequence ASC), so we sort the same way.
        queue_positions: dict[str, int] = {}

        download_types = [TaskType.DOWNLOAD]
        transcript_types = list(ML_LANE_TASK_TYPES)

        queued_by_category: list[tuple[list[TaskRecord], list[TaskType]]] = []
        page_queued_downloads = [
            t for t in tasks if t.status == TaskStatus.QUEUED and t.task_type == TaskType.DOWNLOAD
        ]
        page_queued_transcripts = [
            t for t in tasks if t.status == TaskStatus.QUEUED and t.task_type in ML_LANE_TASK_TYPES
        ]
        if page_queued_downloads:
            queued_by_category.append((page_queued_downloads, download_types))
        if page_queued_transcripts:
            queued_by_category.append((page_queued_transcripts, transcript_types))

        for page_tasks, category_types in queued_by_category:
            # All users, not just user_id: queue position must reflect the
            # global queue, not this caller's filtered view.
            processing_stmt = (
                select(func.count())
                .select_from(TaskRecord)
                .where(
                    and_(
                        TaskRecord.deleted_at.is_(None),
                        TaskRecord.status.in_([TaskStatus.IN_PROGRESS, TaskStatus.POSTPROCESSING]),
                        TaskRecord.task_type.in_(category_types),
                    )
                )
            )
            processing_result = await session.execute(processing_stmt)
            processing_count = processing_result.scalar() or 0

            # priority=None falls back to the default subscription priority.
            default_priority = SUBSCRIPTION_DOWNLOAD_PRIORITY
            seq_stmt = (
                select(TaskRecord.priority, TaskRecord.queue_sequence)
                .where(
                    and_(
                        TaskRecord.deleted_at.is_(None),
                        TaskRecord.status == TaskStatus.QUEUED,
                        TaskRecord.task_type.in_(category_types),
                        TaskRecord.queue_sequence.is_not(None),
                    )
                )
                .order_by(
                    asc(func.coalesce(TaskRecord.priority, default_priority)),
                    asc(TaskRecord.queue_sequence),
                )
            )
            seq_result = await session.execute(seq_stmt)
            all_keys = [(row[0] or default_priority, row[1]) for row in seq_result.all()]

            for task in page_tasks:
                if task.queue_sequence is None:
                    continue
                task_key = (task.priority or default_priority, task.queue_sequence)
                ahead_count = bisect.bisect_left(all_keys, task_key)
                queue_positions[task.task_id] = processing_count + ahead_count

        # One query for the whole page: which of these task_ids have downstream tasks?
        page_task_ids = [t.task_id for t in tasks]
        parents_with_downstream: set[str] = set()
        if page_task_ids:
            # The CASE is load-bearing: upstream_task_ids holds JSON `null` (not SQL NULL)
            # for tasks with no upstream, and jsonb_array_elements_text errors on a scalar.
            # Guarding in WHERE would not do — the set-returning FROM item may run first.
            downstream_stmt = text(
                'SELECT DISTINCT elem FROM task_records, '
                'jsonb_array_elements_text('
                "  CASE WHEN jsonb_typeof(upstream_task_ids::jsonb) = 'array' "
                "       THEN upstream_task_ids::jsonb ELSE '[]'::jsonb END"
                ') AS elem '
                'WHERE elem = ANY(:task_ids)'
            ).bindparams(bindparam('task_ids', value=page_task_ids, type_=ARRAY(String())))
            downstream_result = await session.execute(downstream_stmt)
            parents_with_downstream = {row[0] for row in downstream_result}

        # Imported here, not at module scope: orchestrator.hooks imports this package,
        # so a top-level import would close the cycle (same reason retry.py defers its
        # orchestrator imports).
        from orchestrator.retry import max_retries_for_task_type

        records = []
        for task in tasks:
            task_dict = task.model_dump(mode='json')
            task_dict['has_downstream_tasks'] = task.task_id in parents_with_downstream
            task_dict['queue_position'] = queue_positions.get(task.task_id)
            task_dict['max_retries'] = max_retries_for_task_type(task.task_type)
            records.append(task_dict)

        return {
            'count_records': total_count,
            'page_count': page_count(total_count, page_size),
            'records': records,
        }


async def get_progress_feed(
    user_id: int, *, finished_within: timedelta, limit: int, finished_slots: int
) -> tuple[list[TaskRecord], bool]:
    """A user's active tasks in run order, then those that finished lately, newest first.

    Returns at most `limit` rows and whether any were cut off. Up to `finished_slots` of
    them are held for finished tasks, so a long queue can't hide the outcome of what the
    user just asked for; active tasks get every slot the finished ones leave unused.

    Built for a client that polls every few seconds: two small SELECTs, with no count,
    queue positions or downstream scan like `get_filtered_tasks`. `user_id` is a plain int
    because this feed has no admin view, and an unfiltered query must not be possible by
    accident. SPRITE_GENERATION is left out; it is a chain chore the user never asked for.
    """
    cutoff = (datetime.now(UTC) - finished_within).replace(tzinfo=None)
    owned = (
        TaskRecord.deleted_at.is_(None),
        TaskRecord.user_id == user_id,
        TaskRecord.task_type != TaskType.SPRITE_GENERATION,
    )
    active_stmt = (
        select(TaskRecord)
        .where(*owned, TaskRecord.status.in_(PROGRESS_FEED_ACTIVE_STATUSES))
        .order_by(*_run_order(), desc(TaskRecord.id))
        .limit(limit + 1)
    )
    finished_stmt = (
        select(TaskRecord)
        .where(
            *owned,
            TaskRecord.status.in_(PROGRESS_FEED_FINISHED_STATUSES),
            TaskRecord.updated_at >= cutoff,
        )
        .order_by(desc(TaskRecord.updated_at), desc(TaskRecord.id))
        .limit(limit + 1)
    )
    async with db.get_async_session() as session:
        active = list((await session.execute(active_stmt)).scalars().all())
        finished = list((await session.execute(finished_stmt)).scalars().all())

    finished_kept = min(len(finished), limit, max(finished_slots, limit - len(active)))
    active_kept = min(len(active), limit - finished_kept)
    has_more = active_kept < len(active) or finished_kept < len(finished)
    return active[:active_kept] + finished[:finished_kept], has_more


async def find_one(filter_params: dict) -> TaskRecord | None:
    """Find a single task matching the filter params.

    Supports both equality checks and 'in' queries:
    - Single values: {'status': TaskStatus.QUEUED} -> status == QUEUED
    - List values: {'status': [TaskStatus.QUEUED, TaskStatus.IN_PROGRESS]} -> status IN (...)

    Soft-deleted rows never match; if several rows qualify the newest wins. Callers
    filter on status sets wider than ix_task_records_active_unique's predicate
    (POSTPROCESSING is not in it), so multiple matches are a legal state, not an error.
    """
    async with db.get_async_session() as session:
        stmt = select(TaskRecord)
        conditions = [TaskRecord.deleted_at.is_(None)]

        for key, value in filter_params.items():
            if hasattr(TaskRecord, key):
                column = getattr(TaskRecord, key)
                if isinstance(value, list):
                    conditions.append(column.in_(value))
                else:
                    conditions.append(column == value)

        stmt = stmt.where(and_(*conditions)).order_by(TaskRecord.id.desc()).limit(1)

        result = await session.execute(stmt)
        return result.scalars().first()
