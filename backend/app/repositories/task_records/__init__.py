# Callers can do: from repositories import task_records as tr_repo
# or: from repositories.task_records import insert_task, sync_update_one, ...

from .bulk import bulk_cancel_tasks, bulk_delete_tasks, bulk_retry_tasks
from .crud import (
    DIRECT_DOWNLOAD_PRIORITY,
    SLOT_HOLDING_STATUSES,
    SUBSCRIPTION_DOWNLOAD_PRIORITY,
    find_one,
    get_filtered_tasks,
    get_next_queue_sequence,
    get_progress_feed,
    get_task_by_id,
    get_task_by_task_id,
    get_task_stats,
    get_tasks_by_ids,
    get_tasks_by_task_ids,
    insert_many_tasks,
    insert_task,
    update_one,
)
from .retry import (
    dispatch_download_chain,
    mark_downstream_as_cancelled,
    prioritize_task,
    retry_task_and_downstream_by_id,
)
from .sync_ops import (
    sync_adopt_placeholder,
    sync_delete_tasks_by_ids,
    sync_find_active_by_url_and_type,
    sync_find_latest_not_ready_task,
    sync_find_one,
    sync_get_next_queue_sequence,
    sync_get_task_by_task_id,
    sync_insert_many_tasks,
    sync_insert_task,
    sync_mark_downstream_as_failed,
    sync_mark_downstream_as_not_ready,
    sync_mark_downstream_as_skipped,
    sync_release_cancelled_task_slot,
    sync_retire_placeholder,
    sync_skip_downstream_transcripts,
    sync_soft_delete_not_ready_tasks,
    sync_update_one,
)

# Explicit public surface. Every name above is re-exported on purpose, so
# without this ruff reads the whole module as 32 unused imports (F401).
__all__ = [
    # .crud
    'DIRECT_DOWNLOAD_PRIORITY',
    'SLOT_HOLDING_STATUSES',
    'SUBSCRIPTION_DOWNLOAD_PRIORITY',
    # .bulk
    'bulk_cancel_tasks',
    'bulk_delete_tasks',
    'bulk_retry_tasks',
    # .retry
    'dispatch_download_chain',
    'find_one',
    'get_filtered_tasks',
    'get_next_queue_sequence',
    'get_progress_feed',
    'get_task_by_id',
    'get_task_by_task_id',
    'get_task_stats',
    'get_tasks_by_ids',
    'get_tasks_by_task_ids',
    'insert_many_tasks',
    'insert_task',
    'mark_downstream_as_cancelled',
    'prioritize_task',
    'retry_task_and_downstream_by_id',
    # .sync_ops
    'sync_adopt_placeholder',
    'sync_delete_tasks_by_ids',
    'sync_find_active_by_url_and_type',
    'sync_find_latest_not_ready_task',
    'sync_find_one',
    'sync_get_next_queue_sequence',
    'sync_get_task_by_task_id',
    'sync_insert_many_tasks',
    'sync_insert_task',
    'sync_mark_downstream_as_failed',
    'sync_mark_downstream_as_not_ready',
    'sync_mark_downstream_as_skipped',
    'sync_release_cancelled_task_slot',
    'sync_retire_placeholder',
    'sync_skip_downstream_transcripts',
    'sync_soft_delete_not_ready_tasks',
    'sync_update_one',
    'update_one',
]
