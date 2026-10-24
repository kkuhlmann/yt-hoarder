"""In-process task orchestrator.

Public surface:
    from orchestrator import orch, JobSpec, JobContext, SkipJob, ...

Job bodies live in the tasks/ package; they are registered with the
orchestrator's job registry (jobs.register_job) and executed through
wrapper.run_job_sync inside lane threads (or a spawned ML child process).
"""

from .context import ChildJobError, JobCancelled, JobContext, RetryJob, SkipJob
from .core import (
    Orchestrator,
    OrchestratorNotRunningError,
    map_task_status_to_state,
    orch,
)
from .hooks import (
    BaseStatusHooks,
    ClipHooks,
    DownloadHooks,
    NullHooks,
    SpriteHooks,
    TranscriptHooks,
)
from .jobs import (
    ADD_SUBSCRIPTION_JOB,
    ALL_LANES,
    CLEANUP_JOB,
    CLIP_FROM_SOURCE_JOB,
    CLIP_JOB,
    DEFAULT_LANE,
    DEFAULT_LANE_CONCURRENCY,
    DIRECT_DOWNLOAD_PIPELINE_JOB,
    DOWNLOAD_JOB,
    DOWNLOADS_LANE,
    ML_LANE,
    POPULATE_JOB,
    SPRITES_JOB,
    SUBPROCESS_RUNNER,
    SUBSCRIPTION_CHECK_JOB,
    SUBSCRIPTION_PIPELINE_JOB,
    SUBSCRIPTIONS_LANE,
    THREAD_RUNNER,
    TRANSCRIPT_JOB,
    JobDefinition,
    JobSpec,
    get_job_definition,
    register_job,
)
from .retry import (
    CLIP_RETRY_POLICY,
    DB_RETRY_POLICY,
    DOWNLOAD_RETRY_POLICY,
    TASK_TYPE_RETRY_POLICIES,
    TRANSCRIPT_RETRY_POLICY,
    TRANSIENT_DB_ERRORS,
    RetryPolicy,
    max_retries_for_task_type,
    retry_transient_db,
)

__all__ = [
    'ADD_SUBSCRIPTION_JOB',
    'ALL_LANES',
    'CLEANUP_JOB',
    'CLIP_FROM_SOURCE_JOB',
    'CLIP_JOB',
    'CLIP_RETRY_POLICY',
    'DB_RETRY_POLICY',
    'DEFAULT_LANE',
    'DEFAULT_LANE_CONCURRENCY',
    'DIRECT_DOWNLOAD_PIPELINE_JOB',
    'DOWNLOADS_LANE',
    'DOWNLOAD_JOB',
    'DOWNLOAD_RETRY_POLICY',
    'ML_LANE',
    'POPULATE_JOB',
    'SPRITES_JOB',
    'SUBPROCESS_RUNNER',
    'SUBSCRIPTIONS_LANE',
    'SUBSCRIPTION_CHECK_JOB',
    'SUBSCRIPTION_PIPELINE_JOB',
    'TASK_TYPE_RETRY_POLICIES',
    'THREAD_RUNNER',
    'TRANSCRIPT_JOB',
    'TRANSCRIPT_RETRY_POLICY',
    'TRANSIENT_DB_ERRORS',
    'BaseStatusHooks',
    'ChildJobError',
    'ClipHooks',
    'DownloadHooks',
    'JobCancelled',
    'JobContext',
    'JobDefinition',
    'JobSpec',
    'NullHooks',
    'Orchestrator',
    'OrchestratorNotRunningError',
    'RetryJob',
    'RetryPolicy',
    'SkipJob',
    'SpriteHooks',
    'TranscriptHooks',
    'get_job_definition',
    'map_task_status_to_state',
    'max_retries_for_task_type',
    'orch',
    'register_job',
    'retry_transient_db',
]
