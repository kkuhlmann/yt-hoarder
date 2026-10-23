"""Verify retry wiring on the orchestrator job registry and per-job retry policies."""

import pytest
from sqlalchemy.exc import InterfaceError, OperationalError

from models import TaskType
from orchestrator import (
    ADD_SUBSCRIPTION_JOB,
    ALL_LANES,
    CLEANUP_JOB,
    CLIP_JOB,
    CLIP_RETRY_POLICY,
    DEFAULT_LANE,
    DIRECT_DOWNLOAD_PIPELINE_JOB,
    DOWNLOAD_JOB,
    DOWNLOAD_RETRY_POLICY,
    DOWNLOADS_LANE,
    ML_LANE,
    POPULATE_JOB,
    SPRITES_JOB,
    SUBPROCESS_RUNNER,
    SUBSCRIPTION_CHECK_JOB,
    SUBSCRIPTION_PIPELINE_JOB,
    SUBSCRIPTIONS_LANE,
    TRANSCRIPT_JOB,
    TRANSCRIPT_RETRY_POLICY,
    TRANSIENT_DB_ERRORS,
    ClipHooks,
    DownloadHooks,
    SpriteHooks,
    TranscriptHooks,
    get_job_definition,
)
from orchestrator.jobs import JOB_REGISTRY
from orchestrator.retry import TASK_TYPE_RETRY_POLICIES, max_retries_for_task_type
from tasks import register_all_jobs

# Orchestration bodies that open sync DB sessions and must be wrapped with
# the in-thread transient-DB retry (the DB_RETRY_KWARGS successor).
DB_RETRY_WRAPPED_JOBS = [
    POPULATE_JOB,
    SUBSCRIPTION_PIPELINE_JOB,
    SUBSCRIPTION_CHECK_JOB,
    ADD_SUBSCRIPTION_JOB,
    CLEANUP_JOB,
]


@pytest.fixture(autouse=True)
def _registered():
    register_all_jobs()


def test_transient_db_errors_are_operational_and_interface():
    assert OperationalError in TRANSIENT_DB_ERRORS
    assert InterfaceError in TRANSIENT_DB_ERRORS
    # Must NOT be so broad it retries real bugs (IntegrityError/ProgrammingError).
    from sqlalchemy.exc import DBAPIError

    assert DBAPIError not in TRANSIENT_DB_ERRORS


@pytest.mark.parametrize('job_name', DB_RETRY_WRAPPED_JOBS)
def test_orchestration_bodies_wrapped_with_transient_db_retry(job_name):
    definition = get_job_definition(job_name)
    assert hasattr(definition.fn, '__wrapped__'), (
        f'{job_name} body must be wrapped with retry_transient_db'
    )


def test_direct_download_pipeline_retries_beneath_its_placeholder_cleanup():
    """The retry has to nest *inside* the pipeline's finally, so it can't be registered.

    Retiring a placeholder between DB attempts would leave the next attempt handing off a
    row populate can no longer adopt, so the pipeline registers bare and wraps only its
    inner fan-out.
    """
    from tasks.scheduling import _fan_out_download_chains, run_direct_download_pipeline

    assert get_job_definition(DIRECT_DOWNLOAD_PIPELINE_JOB).fn is run_direct_download_pipeline
    assert hasattr(_fan_out_download_chains, '__wrapped__')


def test_job_lane_assignments():
    """Manual-download work must not share a lane with the subscription pipeline."""
    expected = {
        DOWNLOAD_JOB: DOWNLOADS_LANE,
        TRANSCRIPT_JOB: ML_LANE,
        CLIP_JOB: ML_LANE,
        SPRITES_JOB: ML_LANE,
        POPULATE_JOB: DEFAULT_LANE,
        SUBSCRIPTION_PIPELINE_JOB: SUBSCRIPTIONS_LANE,
        SUBSCRIPTION_CHECK_JOB: SUBSCRIPTIONS_LANE,
        DIRECT_DOWNLOAD_PIPELINE_JOB: DEFAULT_LANE,
        ADD_SUBSCRIPTION_JOB: DEFAULT_LANE,
        CLEANUP_JOB: DEFAULT_LANE,
    }
    assert {name: get_job_definition(name).lane for name in expected} == expected


def test_every_registered_lane_exists():
    """An unknown lane makes submit raise KeyError in a thread, silently dropping the job."""
    assert {d.lane for d in JOB_REGISTRY.values()} <= set(ALL_LANES)


def test_download_job_definition():
    definition = get_job_definition(DOWNLOAD_JOB)
    assert definition.lane == DOWNLOADS_LANE
    assert definition.hooks_factory is DownloadHooks
    # base 300s, doubling, capped at 8h, 20 retries
    assert definition.retry_policy is DOWNLOAD_RETRY_POLICY
    assert DOWNLOAD_RETRY_POLICY.base_delay == 300
    assert DOWNLOAD_RETRY_POLICY.max_delay == 8 * 3600
    assert DOWNLOAD_RETRY_POLICY.max_retries == 20


def test_transcript_job_definition():
    definition = get_job_definition(TRANSCRIPT_JOB)
    assert definition.lane == ML_LANE
    assert definition.runner == SUBPROCESS_RUNNER, 'transcription must run in a spawned child'
    assert definition.child_job == 'transcription'
    assert definition.hooks_factory is TranscriptHooks
    assert definition.retry_policy is TRANSCRIPT_RETRY_POLICY


def test_clip_job_definition():
    definition = get_job_definition(CLIP_JOB)
    assert definition.lane == ML_LANE
    assert definition.hooks_factory is ClipHooks
    assert definition.retry_policy is CLIP_RETRY_POLICY


def test_sprites_job_definition():
    definition = get_job_definition(SPRITES_JOB)
    assert definition.lane == ML_LANE
    assert definition.hooks_factory is SpriteHooks
    # Deliberately no retry policy: sprite failures are deterministic, and a retry
    # would re-take the serial ml slot ahead of the transcript waiting behind it.
    assert definition.retry_policy is None


def test_task_type_policy_map_matches_the_registry():
    """The by-TaskType map serves the UI's attempt ceiling; the registry is the truth."""
    for task_type, job_name in (
        ('DOWNLOAD', DOWNLOAD_JOB),
        ('TRANSCRIPT_GENERATION', TRANSCRIPT_JOB),
        ('CLIP_GENERATION', CLIP_JOB),
    ):
        assert TASK_TYPE_RETRY_POLICIES[task_type] is get_job_definition(job_name).retry_policy


def test_max_retries_for_task_type():
    assert max_retries_for_task_type(TaskType.DOWNLOAD) == 20
    assert max_retries_for_task_type('DOWNLOAD') == 20
    # No retry policy — sprite failures are deterministic and go straight to FAILED.
    assert max_retries_for_task_type(TaskType.SPRITE_GENERATION) is None
