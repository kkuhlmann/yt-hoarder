"""Job specifications and the name → definition registry.

A JobDefinition describes *how* a kind of job runs (which function, which lane,
which lifecycle hooks, which retry policy). A JobSpec describes *one* enqueued
job (its task_id, args, priority). The registry lets startup recovery and the
retry scheduler re-materialize jobs from bare TaskRecord rows.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional

DEFAULT_LANE = 'default'
SUBSCRIPTIONS_LANE = 'subscriptions'
DOWNLOADS_LANE = 'downloads'
ML_LANE = 'ml'

ALL_LANES = (DEFAULT_LANE, SUBSCRIPTIONS_LANE, DOWNLOADS_LANE, ML_LANE)

# Widths used when the caller supplies none, so the orchestrator can start with
# no database at all. The live values come from app_settings; test_lane_concurrency
# pins these two sets together.
DEFAULT_LANE_CONCURRENCY = {
    DEFAULT_LANE: 2,
    SUBSCRIPTIONS_LANE: 1,
    DOWNLOADS_LANE: 1,
    ML_LANE: 1,
}

THREAD_RUNNER = 'thread'
SUBPROCESS_RUNNER = 'subprocess'

# Canonical job names (registry keys; also what recovery re-materializes from
# TaskRecord.task_type)
DOWNLOAD_JOB = 'download_youtube'
TRANSCRIPT_JOB = 'create_transcript_blocks'
POPULATE_JOB = 'populate_media_details'
CLIP_JOB = 'create_clip'
CLIP_FROM_SOURCE_JOB = 'create_clip_from_source'
SPRITES_JOB = 'generate_sprites'
SUBSCRIPTION_PIPELINE_JOB = 'subscription_pipeline'
DIRECT_DOWNLOAD_PIPELINE_JOB = 'direct_download_pipeline'
ADD_SUBSCRIPTION_JOB = 'add_subscription_details'
CLEANUP_JOB = 'cleanup_temp_files'


@dataclass(frozen=True)
class JobDefinition:
    name: str
    # Body: fn(ctx: JobContext, *args) -> retval. Ignored for subprocess jobs
    # (the child resolves `child_job` in its own registry instead).
    fn: Callable | None = None
    lane: str = DEFAULT_LANE
    # Factory returning a hooks instance (see orchestrator.hooks). None → NullHooks.
    hooks_factory: Callable | None = None
    # Automatic-retry policy for RetryJob (see orchestrator.retry). None → no retries.
    retry_policy: Any | None = None
    runner: str = THREAD_RUNNER
    # child_main registry key when runner == SUBPROCESS_RUNNER
    child_job: str | None = None


@dataclass
class JobSpec:
    job_name: str
    args: tuple = ()
    # Pre-assigned UUID for tracked jobs (matches TaskRecord.task_id);
    # generated at submit time when None.
    task_id: str | None = None
    priority: int = 5
    # In-lane ordering (matches TaskRecord.queue_sequence). None on untracked
    # jobs, which sorts them after every sequenced job (lanes.py reads None as
    # +inf); ties then break by submission order.
    queue_sequence: int | None = None
    user_id: int | None = None
    # Tracked jobs have a TaskRecord row; untracked jobs (add-subscription, populate,
    # the pipelines) only exist in the in-memory result registry.
    tracked: bool = True
    # Enqueued (with args=[upstream retval]) when this job succeeds, unless the
    # body set ctx.skip_downstream or returned a NOT_READY result.
    downstream: Optional['JobSpec'] = None
    metadata: dict = field(default_factory=dict)


JOB_REGISTRY: dict[str, JobDefinition] = {}


def register_job(definition: JobDefinition) -> JobDefinition:
    JOB_REGISTRY[definition.name] = definition
    return definition


def get_job_definition(name: str) -> JobDefinition:
    try:
        return JOB_REGISTRY[name]
    except KeyError:
        msg = (
            f'No job named {name!r} is registered with the orchestrator '
            f'(known: {sorted(JOB_REGISTRY)})'
        )
        raise KeyError(msg) from None
