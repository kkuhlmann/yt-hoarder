from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from models import MediaType, TaskRecord, TaskStatus, TaskType, User, utc_now
from repositories import task_records
from repositories import users as user_repo
from repositories.errors import InvalidStateError, NotFoundError

# --- CRUD Operations ---


async def test_insert_task(test_database):
    new_task = TaskRecord(
        task_id='new-test-task-id',
        task_type=TaskType.DOWNLOAD,
        status=TaskStatus.QUEUED,
        percent_complete=0,
        title='New Test Task',
        channel='Test Channel',
        media_type=MediaType.AUDIO,
    )
    result = await task_records.insert_task(new_task)
    assert result is not None
    assert result.id is not None
    assert result.task_id == 'new-test-task-id'
    assert result.status == TaskStatus.QUEUED


async def test_insert_many_tasks(test_database):
    tasks = [
        TaskRecord(
            task_id='bulk-task-1',
            task_type=TaskType.DOWNLOAD,
            status=TaskStatus.QUEUED,
            title='Bulk Task 1',
        ),
        TaskRecord(
            task_id='bulk-task-2',
            task_type=TaskType.TRANSCRIPT_GENERATION,
            status=TaskStatus.QUEUED,
            title='Bulk Task 2',
        ),
    ]
    inserted_ids = await task_records.insert_many_tasks(tasks)
    assert inserted_ids is not None
    assert len(inserted_ids) == 2
    assert all(isinstance(id, int) for id in inserted_ids)


async def test_get_task_by_task_id(test_database):
    task = await task_records.get_task_by_task_id('task-download-complete-1')
    assert task is not None
    assert task.task_id == 'task-download-complete-1'
    assert task.status == TaskStatus.COMPLETE
    assert task.task_type == TaskType.DOWNLOAD


async def test_get_task_by_task_id_not_found(test_database):
    task = await task_records.get_task_by_task_id('nonexistent-task-id')
    assert task is None


async def test_get_task_by_id(test_database):
    task = await task_records.get_task_by_id(1)
    assert task is not None
    assert task.id == 1
    assert task.task_id == 'task-download-complete-1'


async def test_get_task_by_id_not_found(test_database):
    task = await task_records.get_task_by_id(9999)
    assert task is None


async def test_update_one(test_database):
    updated_task = await task_records.update_one(
        'task-download-complete-1',
        {'status_message': 'Updated message', 'percent_complete': 100},
    )
    assert updated_task is not None
    assert updated_task.status_message == 'Updated message'
    assert updated_task.percent_complete == 100


async def test_update_one_not_found(test_database):
    with pytest.raises(ValueError, match='was not found'):
        await task_records.update_one('nonexistent-task-id', {'status_message': 'Test'})


# --- Filtered Task Retrieval ---


async def test_get_filtered_tasks_by_status(test_database):
    result = await task_records.get_filtered_tasks(statuses=['COMPLETE'])
    assert 'count_records' in result
    assert 'records' in result
    assert result['count_records'] >= 2  # At least 2 COMPLETE tasks in test data
    assert all(r['status'] == 'COMPLETE' for r in result['records'])


async def test_get_filtered_tasks_by_multiple_statuses(test_database):
    result = await task_records.get_filtered_tasks(statuses=['QUEUED', 'IN_PROGRESS'])
    assert result['count_records'] >= 2  # 1 QUEUED + 1 IN_PROGRESS in test data
    assert all(r['status'] in ['QUEUED', 'IN_PROGRESS'] for r in result['records'])


async def test_get_filtered_tasks_pagination(test_database):
    # Get first page
    page1 = await task_records.get_filtered_tasks(page=1, page_size=2)
    assert len(page1['records']) == 2

    # Get second page
    page2 = await task_records.get_filtered_tasks(page=2, page_size=2)
    assert len(page2['records']) >= 1

    # Verify different records
    page1_ids = [r['id'] for r in page1['records']]
    page2_ids = [r['id'] for r in page2['records']]
    assert not any(id in page1_ids for id in page2_ids)


async def test_get_filtered_tasks_sorting(test_database):
    # Sort by created_at ascending
    result_asc = await task_records.get_filtered_tasks(sort_by='created_at', sort_direction='asc')
    assert len(result_asc['records']) > 0

    # Sort by created_at descending
    result_desc = await task_records.get_filtered_tasks(sort_by='created_at', sort_direction='desc')
    assert len(result_desc['records']) > 0

    # If we have multiple records, verify ordering differs
    if len(result_asc['records']) > 1:
        assert result_asc['records'][0]['id'] != result_desc['records'][0]['id']


async def test_get_filtered_tasks_default_ordering(test_database):
    # Default ordering: IN_PROGRESS first, then QUEUED, then others
    result = await task_records.get_filtered_tasks(statuses=['IN_PROGRESS', 'QUEUED', 'COMPLETE'])
    records = result['records']

    # Find positions of different status types
    in_progress_indices = [i for i, r in enumerate(records) if r['status'] == 'IN_PROGRESS']
    queued_indices = [i for i, r in enumerate(records) if r['status'] == 'QUEUED']

    # IN_PROGRESS should come before QUEUED
    if in_progress_indices and queued_indices:
        assert max(in_progress_indices) < min(queued_indices)


# --- Find One ---


async def test_find_one(test_database):
    # Filter by status to get a unique match
    task = await task_records.find_one(
        {
            'task_type': TaskType.TRANSCRIPT_GENERATION,
            'status': TaskStatus.COMPLETE,
        }
    )
    assert task is not None
    assert task.task_type == TaskType.TRANSCRIPT_GENERATION
    assert task.status == TaskStatus.COMPLETE


async def test_find_one_multiple_params(test_database):
    task = await task_records.find_one(
        {
            'task_type': TaskType.DOWNLOAD,
            'status': TaskStatus.FAILED,
        }
    )
    assert task is not None
    assert task.task_id == 'task-download-failed-2'


async def test_find_one_not_found(test_database):
    task = await task_records.find_one({'title': 'Nonexistent Title XYZ'})
    assert task is None


# --- Cascade Operations ---


async def test_mark_downstream_as_cancelled(test_database):
    # Create a new task with upstream dependency for testing
    new_task = TaskRecord(
        task_id='test-downstream-cancel',
        task_type=TaskType.TRANSCRIPT_GENERATION,
        status=TaskStatus.QUEUED,
        upstream_task_ids=['task-download-queued-5'],
    )
    await task_records.insert_task(new_task)

    # Mark downstream as cancelled
    modified_count = await task_records.mark_downstream_as_cancelled('task-download-queued-5')
    assert modified_count >= 1

    # Verify the downstream task is now CANCELLED
    downstream = await task_records.get_task_by_task_id('test-downstream-cancel')
    assert downstream.status == TaskStatus.CANCELLED
    assert 'upstream' in downstream.status_message.lower()


# --- Bulk Operations ---


async def test_bulk_cancel_tasks(test_database):
    # Create tasks to cancel
    tasks = [
        TaskRecord(
            task_id='bulk-cancel-1',
            task_type=TaskType.DOWNLOAD,
            status=TaskStatus.QUEUED,
        ),
        TaskRecord(
            task_id='bulk-cancel-2',
            task_type=TaskType.DOWNLOAD,
            status=TaskStatus.QUEUED,
        ),
    ]
    await task_records.insert_many_tasks(tasks)

    # Bulk cancel
    result = await task_records.bulk_cancel_tasks(['bulk-cancel-1', 'bulk-cancel-2'])
    assert result['cancelled_count'] == 2
    assert 'errors' in result

    # Verify cancellation
    task1 = await task_records.get_task_by_task_id('bulk-cancel-1')
    task2 = await task_records.get_task_by_task_id('bulk-cancel-2')
    assert task1.status == TaskStatus.CANCELLED
    assert task2.status == TaskStatus.CANCELLED


async def test_bulk_delete_tasks(test_database):
    # Create tasks to delete
    tasks = [
        TaskRecord(
            task_id='bulk-delete-1',
            task_type=TaskType.DOWNLOAD,
            status=TaskStatus.COMPLETE,
        ),
        TaskRecord(
            task_id='bulk-delete-2',
            task_type=TaskType.DOWNLOAD,
            status=TaskStatus.COMPLETE,
        ),
    ]
    inserted_ids = await task_records.insert_many_tasks(tasks)

    # Bulk delete (soft delete - sets deleted_at timestamp)
    result = await task_records.bulk_delete_tasks(inserted_ids)
    assert result['deleted_count'] == 2
    assert 'errors' in result

    # Note: Soft delete doesn't remove records, just sets deleted_at
    # The tasks still exist but are filtered out of normal queries


async def test_bulk_delete_tasks_empty(test_database):
    result = await task_records.bulk_delete_tasks([])
    assert result['deleted_count'] == 0


# --- Sync Function Tests ---


def test_sync_insert_task(test_database):
    new_task = TaskRecord(
        task_id='sync-insert-test',
        task_type=TaskType.DOWNLOAD,
        status=TaskStatus.QUEUED,
        title='Sync Insert Test',
    )
    result = task_records.sync_insert_task(new_task)
    assert result is not None
    assert result.id is not None
    assert result.task_id == 'sync-insert-test'


def test_sync_mark_downstream_as_failed(test_database):
    # Create a task with upstream dependency
    new_task = TaskRecord(
        task_id='sync-downstream-test',
        task_type=TaskType.TRANSCRIPT_GENERATION,
        status=TaskStatus.QUEUED,
        upstream_task_ids=['task-download-progress-6'],
    )
    task_records.sync_insert_task(new_task)

    # Mark downstream as failed
    modified_count = task_records.sync_mark_downstream_as_failed('task-download-progress-6')
    assert modified_count >= 1

    # Verify
    task = task_records.sync_get_task_by_task_id('sync-downstream-test')
    assert task.status == TaskStatus.UPSTREAM_FAILED


def test_mark_downstream_skips_terminal_statuses(test_database):
    # task-transcript-upstream-4 is already UPSTREAM_FAILED (terminal) — must not be touched
    assert task_records.sync_mark_downstream_as_failed('task-download-failed-2') == 0


def test_mark_downstream_marks_active_children(test_database):
    task_records.sync_update_one('task-transcript-complete-3', {'status': TaskStatus.QUEUED})

    assert task_records.sync_mark_downstream_as_failed('task-download-complete-1') == 1

    child = task_records.sync_get_task_by_task_id('task-transcript-complete-3')
    assert child.status == TaskStatus.UPSTREAM_FAILED
    # UPSTREAM_FAILED is the one downstream marking that writes no status_message
    assert child.status_message is None


def test_sync_mark_downstream_as_not_ready(test_database):
    task_records.sync_update_one('task-transcript-complete-3', {'status': TaskStatus.QUEUED})

    assert task_records.sync_mark_downstream_as_not_ready('task-download-complete-1') == 1

    child = task_records.sync_get_task_by_task_id('task-transcript-complete-3')
    assert child.status == TaskStatus.NOT_READY
    assert child.status_message == 'Upstream video not ready for download yet'

    # NOT_READY is itself excluded, so a second sweep is a no-op
    assert task_records.sync_mark_downstream_as_not_ready('task-download-complete-1') == 0


# --- NOT_READY placeholder helpers ---


NOT_READY_URL = 'https://www.youtube.com/watch?v=notready1'


def _make_not_ready_task(task_id: str, **overrides) -> TaskRecord:
    defaults = {
        'task_id': task_id,
        'task_type': TaskType.DOWNLOAD,
        'status': TaskStatus.NOT_READY,
        'download_job_url': NOT_READY_URL,
        'media_type': MediaType.AUDIO,
    }
    defaults.update(overrides)
    return TaskRecord(**defaults)


def test_sync_find_latest_not_ready_task(test_database):
    """Four independent selection rules, each on its own URL so that the live-placeholder
    unique index can't let one scenario interfere with the next."""
    newest_url = NOT_READY_URL + '-newest'
    deleted_url = NOT_READY_URL + '-deleted'
    queued_url = NOT_READY_URL + '-queued'
    untyped_url = NOT_READY_URL + '-untyped'

    # Newest wins. Duplicate *live* placeholders are blocked by
    # ix_task_records_not_ready_unique; an older soft-deleted one may still coexist.
    task_records.sync_insert_task(_make_not_ready_task('nr-old', download_job_url=newest_url))
    task_records.sync_update_one('nr-old', {'deleted_at': utc_now()})
    task_records.sync_insert_task(_make_not_ready_task('nr-new', download_job_url=newest_url))
    newest = task_records.sync_find_latest_not_ready_task(newest_url, 'AUDIO')
    assert newest is not None
    assert newest.task_id == 'nr-new'

    # Soft-deleted rows are excluded outright.
    task_records.sync_insert_task(_make_not_ready_task('nr-deleted', download_job_url=deleted_url))
    task_records.sync_update_one('nr-deleted', {'deleted_at': utc_now()})
    assert task_records.sync_find_latest_not_ready_task(deleted_url, 'AUDIO') is None

    # Only NOT_READY qualifies.
    task_records.sync_insert_task(
        _make_not_ready_task('nr-queued', download_job_url=queued_url, status=TaskStatus.QUEUED)
    )
    assert task_records.sync_find_latest_not_ready_task(queued_url, 'AUDIO') is None

    # media_type=None matches the untyped row, not the AUDIO one sharing its URL.
    task_records.sync_insert_task(_make_not_ready_task('nr-audio', download_job_url=untyped_url))
    task_records.sync_insert_task(
        _make_not_ready_task('nr-untyped', download_job_url=untyped_url, media_type=None)
    )
    untyped = task_records.sync_find_latest_not_ready_task(untyped_url, None)
    assert untyped is not None
    assert untyped.task_id == 'nr-untyped'


def test_sync_soft_delete_not_ready_tasks(test_database):
    task_records.sync_insert_task(_make_not_ready_task('not-ready-dl'))
    task_records.sync_insert_task(
        _make_not_ready_task('not-ready-tr', task_type=TaskType.TRANSCRIPT_GENERATION)
    )
    task_records.sync_insert_task(
        _make_not_ready_task('not-ready-active', status=TaskStatus.QUEUED)
    )

    count = task_records.sync_soft_delete_not_ready_tasks(NOT_READY_URL, 'AUDIO')
    assert count == 2

    assert task_records.sync_get_task_by_task_id('not-ready-dl').deleted_at is not None
    assert task_records.sync_get_task_by_task_id('not-ready-tr').deleted_at is not None
    assert task_records.sync_get_task_by_task_id('not-ready-active').deleted_at is None


def test_sync_soft_delete_not_ready_tasks_none_matching(test_database):
    count = task_records.sync_soft_delete_not_ready_tasks(NOT_READY_URL, 'AUDIO')
    assert count == 0


async def test_get_task_stats_counts_not_ready(test_database):
    task_records.sync_insert_task(_make_not_ready_task('stats-not-ready'))

    stats = await task_records.get_task_stats()
    assert stats['not_ready'] == 1


async def test_get_task_stats_counts_queued_clip_generation_as_transcripts(test_database):
    task_records.sync_insert_task(
        TaskRecord(
            task_id='stats-queued-clip',
            task_type=TaskType.CLIP_GENERATION,
            status=TaskStatus.QUEUED,
        )
    )

    stats = await task_records.get_task_stats()
    assert stats['queued_transcripts'] == 1
    assert stats['queued_total'] == stats['queued_downloads'] + stats['queued_transcripts']


async def test_get_filtered_tasks_not_ready_sorts_before_history(test_database):
    # A COMPLETE row created after the NOT_READY row would win the created_at
    # tiebreak if both shared a sort bucket — NOT_READY must get its own bucket.
    task_records.sync_insert_task(_make_not_ready_task('sort-not-ready'))
    task_records.sync_insert_task(
        _make_not_ready_task('sort-complete-newer', status=TaskStatus.COMPLETE)
    )

    result = await task_records.get_filtered_tasks(statuses=['NOT_READY', 'COMPLETE'])
    task_ids = [r['task_id'] for r in result['records']]
    assert task_ids.index('sort-not-ready') < task_ids.index('sort-complete-newer')


async def test_sync_mark_downstream_as_skipped(test_database):
    # Create a queued transcript task depending on a seeded queued download
    new_task = TaskRecord(
        task_id='test-downstream-skip',
        task_type=TaskType.TRANSCRIPT_GENERATION,
        status=TaskStatus.QUEUED,
        upstream_task_ids=['task-download-queued-5'],
    )
    await task_records.insert_task(new_task)

    modified_count = task_records.sync_mark_downstream_as_skipped('task-download-queued-5')
    assert modified_count >= 1

    downstream = await task_records.get_task_by_task_id('test-downstream-skip')
    assert downstream.status == TaskStatus.SKIPPED
    assert 'storage limit' in downstream.status_message.lower()


# --- ix_task_records_active_unique / CANCELLED slot release ---
# The index is partial (postgresql_where), so what it does and does not count as
# "active" is only observable against real Postgres.


def _sprite_row(task_id: str, status: TaskStatus) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        task_type=TaskType.SPRITE_GENERATION,
        status=status,
        download_job_url='https://example.com/watch?v=sprite',
        media_type=MediaType.VIDEO,
    )


async def test_cancelled_row_blocks_a_new_active_task(test_database):
    """Pins the trap sync_release_cancelled_task_slot exists to work around."""
    await task_records.insert_task(_sprite_row('sprite-cancelled', TaskStatus.CANCELLED))

    with pytest.raises(IntegrityError):
        task_records.sync_insert_task(_sprite_row('sprite-new', TaskStatus.QUEUED))


async def test_release_cancelled_slot_allows_reinsert(test_database):
    await task_records.insert_task(_sprite_row('sprite-cancelled-2', TaskStatus.CANCELLED))

    released = task_records.sync_release_cancelled_task_slot(
        'https://example.com/watch?v=sprite', MediaType.VIDEO.value, TaskType.SPRITE_GENERATION
    )
    assert released == 1

    task_records.sync_insert_task(_sprite_row('sprite-new-2', TaskStatus.QUEUED))

    retired = await task_records.get_task_by_task_id('sprite-cancelled-2')
    assert retired.status == TaskStatus.DELETED
    assert retired.deleted_at is not None


async def test_bulk_delete_frees_the_slot_a_cancelled_row_holds(test_database):
    """Deleting a cancelled task must make the URL submittable again.

    deleted_at alone leaves the row inside the index predicate, so the next submission
    still collides with a task the user can no longer see, retry or delete.
    """
    cancelled = _sprite_row('sprite-deleted-cancelled', TaskStatus.CANCELLED)
    await task_records.insert_task(cancelled)

    result = await task_records.bulk_delete_tasks([cancelled.id])
    assert result['deleted_count'] == 1

    task_records.sync_insert_task(_sprite_row('sprite-after-delete', TaskStatus.QUEUED))

    retired = await task_records.get_task_by_task_id('sprite-deleted-cancelled')
    assert retired.status == TaskStatus.DELETED
    assert retired.deleted_at is not None


async def test_bulk_delete_leaves_a_terminal_status_intact(test_database):
    """A COMPLETE row never held the slot, so its status is history worth keeping."""
    done = _sprite_row('sprite-complete', TaskStatus.COMPLETE)
    await task_records.insert_task(done)

    await task_records.bulk_delete_tasks([done.id])

    deleted = await task_records.get_task_by_task_id('sprite-complete')
    assert deleted.status == TaskStatus.COMPLETE
    assert deleted.deleted_at is not None


async def test_release_cancelled_slot_leaves_other_task_types_alone(test_database):
    """A cancelled *download* must keep blocking — the next tick would resurrect it."""
    await task_records.insert_task(
        TaskRecord(
            task_id='download-cancelled',
            task_type=TaskType.DOWNLOAD,
            status=TaskStatus.CANCELLED,
            download_job_url='https://example.com/watch?v=sprite',
            media_type=MediaType.VIDEO,
        )
    )

    released = task_records.sync_release_cancelled_task_slot(
        'https://example.com/watch?v=sprite', MediaType.VIDEO.value, TaskType.SPRITE_GENERATION
    )

    assert released == 0
    assert (await task_records.get_task_by_task_id('download-cancelled')).status == (
        TaskStatus.CANCELLED
    )


# --- ml-lane grouping (ML_LANE_TASK_TYPES) ---
# Sprites share the serial ml lane with transcripts and clips, so they must be
# counted and positioned against that queue, not the downloads queue.


async def test_get_task_stats_counts_queued_sprites_as_transcripts(test_database):
    task_records.sync_insert_task(
        TaskRecord(
            task_id='stats-queued-sprite',
            task_type=TaskType.SPRITE_GENERATION,
            status=TaskStatus.QUEUED,
        )
    )

    stats = await task_records.get_task_stats()
    assert stats['queued_transcripts'] == 1
    assert stats['queued_total'] == stats['queued_downloads'] + stats['queued_transcripts']


async def test_queued_sprite_takes_a_position_in_the_ml_queue(test_database):
    for task_id, task_type, seq in (
        ('pos-transcript', TaskType.TRANSCRIPT_GENERATION, 9001),
        ('pos-sprite', TaskType.SPRITE_GENERATION, 9002),
    ):
        task_records.sync_insert_task(
            TaskRecord(
                task_id=task_id,
                task_type=task_type,
                status=TaskStatus.QUEUED,
                queue_sequence=seq,
                priority=5,
            )
        )

    result = await task_records.get_filtered_tasks(statuses=['QUEUED'], page_size=100)
    positions = {r['task_id']: r['queue_position'] for r in result['records']}

    # The sprite sits directly behind the transcript, not in the downloads queue.
    assert positions['pos-sprite'] == positions['pos-transcript'] + 1


# --- downstream sweeps and the sprite sibling ---


def _chain_rows(download_task_id: str, url: str) -> list[TaskRecord]:
    return [
        TaskRecord(
            task_id=f'{download_task_id}-tr',
            task_type=TaskType.TRANSCRIPT_GENERATION,
            status=TaskStatus.QUEUED,
            upstream_task_ids=[download_task_id],
            download_job_url=url,
            media_type=MediaType.VIDEO,
        ),
        TaskRecord(
            task_id=f'{download_task_id}-spr',
            task_type=TaskType.SPRITE_GENERATION,
            status=TaskStatus.QUEUED,
            upstream_task_ids=[download_task_id],
            download_job_url=url,
            media_type=MediaType.VIDEO,
        ),
    ]


async def test_skip_downstream_transcripts_leaves_the_sprite_row_alone(test_database):
    """The whole point of the task_types filter.

    on_success dispatches the sprite row microseconds after the body returns; an
    unfiltered sweep would mark it SKIPPED first and the sheet would never build.
    """
    for row in _chain_rows('dl-skip', 'https://example.com/watch?v=skip'):
        await task_records.insert_task(row)

    marked = task_records.sync_skip_downstream_transcripts('dl-skip', 'Skipped - already there')

    assert marked == 1
    assert (await task_records.get_task_by_task_id('dl-skip-tr')).status == TaskStatus.SKIPPED
    assert (await task_records.get_task_by_task_id('dl-skip-spr')).status == TaskStatus.QUEUED


async def test_mark_downstream_as_failed_still_sweeps_sprites(test_database):
    """Unfiltered by design — a failed download must take its whole chain with it."""
    for row in _chain_rows('dl-fail', 'https://example.com/watch?v=fail'):
        await task_records.insert_task(row)

    task_records.sync_mark_downstream_as_failed('dl-fail')

    for suffix in ('tr', 'spr'):
        task = await task_records.get_task_by_task_id(f'dl-fail-{suffix}')
        assert task.status == TaskStatus.UPSTREAM_FAILED


def _make_resolving_task(task_id: str, url: str) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        task_type=TaskType.DOWNLOAD,
        status=TaskStatus.RESOLVING,
        status_message='Fetching video metadata...',
        title=url,
        media_type=MediaType.VIDEO,
        download_job_url=url,
    )


async def test_resolving_sorts_above_queued(test_database):
    """A just-submitted row has no queue_sequence, so it needs its own sort bucket.

    Sharing QUEUED's bucket would put it behind every dispatched row (nullslast) —
    pages away from the user who just submitted it.
    """
    task_records.sync_insert_task(
        _make_resolving_task('sort-resolving', 'https://www.youtube.com/watch?v=SoRtReS001')
    )

    result = await task_records.get_filtered_tasks(statuses=['RESOLVING', 'QUEUED'])
    statuses = [r['status'] for r in result['records']]

    assert 'RESOLVING' in statuses and 'QUEUED' in statuses
    assert statuses.index('RESOLVING') < statuses.index('QUEUED')


async def test_resolving_has_no_queue_position(test_database):
    task_records.sync_insert_task(
        _make_resolving_task('pos-resolving', 'https://www.youtube.com/watch?v=SoRtReS002')
    )

    result = await task_records.get_filtered_tasks(statuses=['RESOLVING'])
    row = next(r for r in result['records'] if r['task_id'] == 'pos-resolving')

    assert row['queue_position'] is None


async def test_get_task_stats_counts_resolving_as_queued_download(test_database):
    """The Queued chip filters on QUEUED+RESOLVING, so the stat tile must agree."""
    before = (await task_records.get_task_stats())['queued_downloads']
    task_records.sync_insert_task(
        _make_resolving_task('stats-resolving', 'https://www.youtube.com/watch?v=SoRtReS003')
    )

    stats = await task_records.get_task_stats()
    assert stats['queued_downloads'] == before + 1
    assert stats['queued_total'] == stats['queued_downloads'] + stats['queued_transcripts']


async def test_serialized_rows_carry_the_attempt_ceiling(test_database):
    """The Tasks UI renders "(3/20)", and 20 lives only in the retry policy."""
    task_records.sync_insert_task(
        TaskRecord(
            task_id='ceiling-download',
            task_type=TaskType.DOWNLOAD,
            status=TaskStatus.RETRY,
            retry_count=3,
            next_retry_at=utc_now(),
        )
    )
    task_records.sync_insert_task(
        TaskRecord(
            task_id='ceiling-sprite',
            task_type=TaskType.SPRITE_GENERATION,
            status=TaskStatus.QUEUED,
        )
    )

    result = await task_records.get_filtered_tasks(statuses=['RETRY', 'QUEUED'])
    rows = {r['task_id']: r for r in result['records']}

    assert rows['ceiling-download']['max_retries'] == 20
    assert rows['ceiling-download']['retry_count'] == 3
    # Sprites have no retry policy at all, so there is no ceiling to show.
    assert rows['ceiling-sprite']['max_retries'] is None


# --- prioritize_task ---


async def test_prioritize_task_moves_queued_download_to_front(test_database):
    record = await task_records.insert_task(
        TaskRecord(
            task_id='prioritize-repo-1',
            task_type=TaskType.DOWNLOAD,
            status=TaskStatus.QUEUED,
            title='Prioritize Me',
            priority=5,
            queue_sequence=42,
        )
    )

    result = await task_records.prioritize_task(record.id)

    assert result['status'] == 'prioritized'
    assert result['record_id'] == record.id
    assert result['new_task_id'] == 'prioritize-repo-1'

    updated = await task_records.get_task_by_id(record.id)
    assert updated.priority == 0
    assert updated.queue_sequence == 0
    assert updated.status_message == 'Prioritized'


async def test_prioritize_task_not_found(test_database):
    with pytest.raises(NotFoundError):
        await task_records.prioritize_task(999999)


async def test_prioritize_task_rejects_non_queued(test_database):
    # Seed row id=1 is a COMPLETE download.
    with pytest.raises(InvalidStateError):
        await task_records.prioritize_task(1)


async def test_prioritize_task_rejects_non_download(test_database):
    record = await task_records.insert_task(
        TaskRecord(
            task_id='prioritize-repo-transcript',
            task_type=TaskType.TRANSCRIPT_GENERATION,
            status=TaskStatus.QUEUED,
        )
    )
    with pytest.raises(InvalidStateError):
        await task_records.prioritize_task(record.id)


# --- find_one / sync_find_one: multiple matches and soft deletes ---


async def test_find_one_tolerates_multiple_matches_and_prefers_newest(test_database):
    # POSTPROCESSING is outside ix_task_records_active_unique's predicate, so a
    # second active row for the same URL is a legal state; find_one must pick one
    # (the newest), not raise MultipleResultsFound.
    url = 'https://www.youtube.com/watch?v=findone1'
    await task_records.insert_task(
        TaskRecord(
            task_id='find-one-postprocessing',
            task_type=TaskType.DOWNLOAD,
            status=TaskStatus.POSTPROCESSING,
            download_job_url=url,
            media_type=MediaType.VIDEO,
        )
    )
    await task_records.insert_task(
        TaskRecord(
            task_id='find-one-queued',
            task_type=TaskType.DOWNLOAD,
            status=TaskStatus.QUEUED,
            download_job_url=url,
            media_type=MediaType.VIDEO,
        )
    )

    found = await task_records.find_one(
        {
            'task_type': TaskType.DOWNLOAD,
            'download_job_url': url,
            'media_type': MediaType.VIDEO,
            'status': [TaskStatus.POSTPROCESSING, TaskStatus.QUEUED],
        }
    )
    assert found is not None
    assert found.task_id == 'find-one-queued'

    sync_found = task_records.sync_find_one(
        {
            'task_type': TaskType.DOWNLOAD,
            'download_job_url': url,
            'media_type': MediaType.VIDEO,
            'status': [TaskStatus.POSTPROCESSING, TaskStatus.QUEUED],
        }
    )
    assert sync_found is not None
    assert sync_found.task_id == 'find-one-queued'


async def test_find_one_ignores_soft_deleted_rows(test_database):
    url = 'https://www.youtube.com/watch?v=findone2'
    await task_records.insert_task(
        TaskRecord(
            task_id='find-one-soft-deleted',
            task_type=TaskType.DOWNLOAD,
            status=TaskStatus.QUEUED,
            download_job_url=url,
            media_type=MediaType.VIDEO,
            deleted_at=utc_now(),
        )
    )

    found = await task_records.find_one(
        {
            'task_type': TaskType.DOWNLOAD,
            'download_job_url': url,
            'media_type': MediaType.VIDEO,
            'status': [TaskStatus.QUEUED],
        }
    )
    assert found is None
    assert (
        task_records.sync_find_one(
            {
                'task_type': TaskType.DOWNLOAD,
                'download_job_url': url,
                'media_type': MediaType.VIDEO,
                'status': [TaskStatus.QUEUED],
            }
        )
        is None
    )


# --- Progress feed ---

FEED_WINDOW = timedelta(hours=1)


async def _feed_user(username: str) -> int:
    user = await user_repo.create_user(User(username=username, password_hash='x', is_approved=True))
    return user.id


async def _feed_task(
    task_id: str,
    user_id: int,
    status: TaskStatus,
    *,
    task_type: TaskType = TaskType.DOWNLOAD,
    age: timedelta = timedelta(0),
    **fields,
) -> None:
    await task_records.insert_task(
        TaskRecord(
            task_id=task_id,
            task_type=task_type,
            status=status,
            user_id=user_id,
            updated_at=utc_now() - age,
            **fields,
        )
    )


async def _feed(user_id: int, limit: int = 25, finished_slots: int = 10):
    return await task_records.get_progress_feed(
        user_id, finished_within=FEED_WINDOW, limit=limit, finished_slots=finished_slots
    )


async def _feed_ids(user_id: int, limit: int = 25, finished_slots: int = 10) -> list[str]:
    rows, _ = await _feed(user_id, limit, finished_slots)
    return [row.task_id for row in rows]


async def test_progress_feed_holds_active_and_recently_finished_tasks(clean_database):
    uid = await _feed_user('feed-owner')
    for status in (
        TaskStatus.RESOLVING,
        TaskStatus.QUEUED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.POSTPROCESSING,
        TaskStatus.RETRY,
        TaskStatus.NOT_READY,
        TaskStatus.COMPLETE,
        TaskStatus.FAILED,
    ):
        await _feed_task(f'in-{status.value}', uid, status)

    assert set(await _feed_ids(uid)) == {
        'in-RESOLVING',
        'in-QUEUED',
        'in-IN_PROGRESS',
        'in-POSTPROCESSING',
        'in-RETRY',
        'in-NOT_READY',
        'in-COMPLETE',
        'in-FAILED',
    }


async def test_progress_feed_leaves_out_what_the_user_did_not_ask_to_see(clean_database):
    uid = await _feed_user('feed-owner')
    other = await _feed_user('feed-other')
    await _feed_task('kept', uid, TaskStatus.IN_PROGRESS)
    await _feed_task('someone-elses', other, TaskStatus.IN_PROGRESS)
    await _feed_task('old-complete', uid, TaskStatus.COMPLETE, age=timedelta(hours=2))
    await _feed_task('old-failed', uid, TaskStatus.FAILED, age=timedelta(hours=2))
    await _feed_task('skipped', uid, TaskStatus.SKIPPED)
    await _feed_task('cancelled', uid, TaskStatus.CANCELLED)
    await _feed_task('upstream-failed', uid, TaskStatus.UPSTREAM_FAILED)
    await _feed_task('soft-deleted', uid, TaskStatus.IN_PROGRESS, deleted_at=utc_now())
    await _feed_task('sprite', uid, TaskStatus.IN_PROGRESS, task_type=TaskType.SPRITE_GENERATION)

    assert await _feed_ids(uid) == ['kept']


async def test_progress_feed_with_no_tasks_is_empty(clean_database):
    uid = await _feed_user('feed-owner')

    rows, has_more = await _feed(uid)

    assert rows == []
    assert has_more is False


async def test_progress_feed_ranks_active_before_finished_and_finished_by_recency(clean_database):
    uid = await _feed_user('feed-owner')
    await _feed_task('complete-older', uid, TaskStatus.COMPLETE, age=timedelta(minutes=30))
    await _feed_task('failed', uid, TaskStatus.FAILED, age=timedelta(minutes=10))
    await _feed_task('complete-newer', uid, TaskStatus.COMPLETE, age=timedelta(minutes=1))
    await _feed_task('queued', uid, TaskStatus.QUEUED, age=timedelta(minutes=45))
    await _feed_task('running', uid, TaskStatus.IN_PROGRESS, age=timedelta(minutes=50))
    await _feed_task('resolving', uid, TaskStatus.RESOLVING, age=timedelta(minutes=55))

    assert await _feed_ids(uid) == [
        'running',
        'resolving',
        'queued',
        'complete-newer',
        'failed',
        'complete-older',
    ]


async def test_progress_feed_reports_when_it_cut_rows_off(clean_database):
    uid = await _feed_user('feed-owner')
    for n in range(3):
        await _feed_task(f'task-{n}', uid, TaskStatus.QUEUED, queue_sequence=n)

    rows, has_more = await _feed(uid, limit=2)
    exact_rows, exact_has_more = await _feed(uid, limit=3)

    assert [row.task_id for row in rows] == ['task-0', 'task-1']
    assert has_more is True
    assert len(exact_rows) == 3
    assert exact_has_more is False


async def test_progress_feed_lists_queued_tasks_in_the_order_they_will_run(clean_database):
    uid = await _feed_user('feed-owner')
    await _feed_task('background-first', uid, TaskStatus.QUEUED, priority=5, queue_sequence=1)
    await _feed_task('manual-later', uid, TaskStatus.QUEUED, priority=1, queue_sequence=3)
    await _feed_task('manual-earlier', uid, TaskStatus.QUEUED, priority=1, queue_sequence=2)

    assert await _feed_ids(uid) == ['manual-earlier', 'manual-later', 'background-first']


async def test_progress_feed_holds_slots_for_finished_tasks_behind_a_long_queue(clean_database):
    uid = await _feed_user('feed-owner')
    for n in range(5):
        await _feed_task(f'queued-{n}', uid, TaskStatus.QUEUED, queue_sequence=n)
    await _feed_task('just-finished', uid, TaskStatus.COMPLETE)

    rows, has_more = await _feed(uid, limit=4, finished_slots=2)

    assert [row.task_id for row in rows] == ['queued-0', 'queued-1', 'queued-2', 'just-finished']
    assert has_more is True


async def test_progress_feed_caps_finished_tasks_at_their_slots_when_the_queue_is_full(
    clean_database,
):
    uid = await _feed_user('feed-owner')
    for n in range(5):
        await _feed_task(f'queued-{n}', uid, TaskStatus.QUEUED, queue_sequence=n)
    for n in range(5):
        await _feed_task(f'finished-{n}', uid, TaskStatus.COMPLETE, age=timedelta(minutes=n))

    rows, has_more = await _feed(uid, limit=4, finished_slots=2)

    assert [row.task_id for row in rows] == ['queued-0', 'queued-1', 'finished-0', 'finished-1']
    assert has_more is True


async def test_progress_feed_gives_finished_tasks_the_slots_active_ones_leave(clean_database):
    uid = await _feed_user('feed-owner')
    await _feed_task('running', uid, TaskStatus.IN_PROGRESS)
    for n in range(4):
        await _feed_task(f'finished-{n}', uid, TaskStatus.FAILED, age=timedelta(minutes=n))

    rows, has_more = await _feed(uid, limit=4, finished_slots=1)

    assert [row.task_id for row in rows] == ['running', 'finished-0', 'finished-1', 'finished-2']
    assert has_more is True
