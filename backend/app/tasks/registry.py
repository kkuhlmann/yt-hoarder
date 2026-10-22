"""Registers every job body with the orchestrator.

Called once from the FastAPI lifespan before orch.start().

Note: the transcription job has no parent-side function — it runs in a spawned
child process (orchestrator.child_main resolves 'transcription' and imports
tasks.transcription there), so the parent process never imports faster-whisper
at all.
"""

from functools import partial

from orchestrator import (
    ADD_SUBSCRIPTION_JOB,
    CLEANUP_JOB,
    CLIP_FROM_SOURCE_JOB,
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
    SUBSCRIPTION_PIPELINE_JOB,
    SUBSCRIPTIONS_LANE,
    TRANSCRIPT_JOB,
    TRANSCRIPT_RETRY_POLICY,
    ClipHooks,
    DownloadHooks,
    JobDefinition,
    SpriteHooks,
    TranscriptHooks,
    register_job,
    retry_transient_db,
)


def register_all_jobs() -> None:
    from tasks.clips import run_clip_job, run_source_clip_job
    from tasks.downloads import run_download_job
    from tasks.media import guard_resolving_placeholders, run_populate_media_details
    from tasks.scheduling import run_cleanup_job, run_direct_download_pipeline
    from tasks.sprites import run_sprites_job
    from tasks.subscriptions import run_add_subscription, run_subscription_pipeline

    register_job(
        JobDefinition(
            name=DOWNLOAD_JOB,
            fn=run_download_job,
            lane=DOWNLOADS_LANE,
            hooks_factory=DownloadHooks,
            retry_policy=DOWNLOAD_RETRY_POLICY,
        )
    )
    register_job(
        JobDefinition(
            name=TRANSCRIPT_JOB,
            lane=ML_LANE,
            hooks_factory=TranscriptHooks,
            retry_policy=TRANSCRIPT_RETRY_POLICY,
            runner=SUBPROCESS_RUNNER,
            child_job='transcription',
        )
    )
    register_job(
        JobDefinition(
            name=CLIP_JOB,
            fn=run_clip_job,
            lane=ML_LANE,
            hooks_factory=ClipHooks,
            retry_policy=CLIP_RETRY_POLICY,
        )
    )
    # A clip of an item with no file is fetched from YouTube, so it is paced with the
    # downloads rather than queued behind transcription on the ml lane.
    register_job(
        JobDefinition(
            name=CLIP_FROM_SOURCE_JOB,
            fn=run_source_clip_job,
            lane=DOWNLOADS_LANE,
            hooks_factory=ClipHooks,
            retry_policy=CLIP_RETRY_POLICY,
        )
    )
    # No retry_policy: sprite failures are deterministic (missing file, no duration,
    # unsupported codec), so retrying would burn the serial ml slot ahead of the
    # transcript. The Tasks UI retry button re-dispatches deliberate cases instead.
    register_job(
        JobDefinition(name=SPRITES_JOB, fn=run_sprites_job, lane=ML_LANE, hooks_factory=SpriteHooks)
    )

    # Orchestration bodies: transient DB errors are retried in-thread with a
    # jittered backoff (they open sync DB sessions from lane threads).
    # guard_resolving_placeholders is only for a body that owns a submission's RESOLVING
    # row through to resolution — populate does, and it wraps *outside* retry_transient_db
    # because retiring the row between DB retries would leave the retry unable to adopt it.
    # DIRECT_DOWNLOAD_PIPELINE_JOB is unguarded on purpose: it hands its rows to populate
    # jobs and returns first, so it does its own handoff-aware cleanup instead.
    register_job(
        JobDefinition(
            name=POPULATE_JOB,
            fn=guard_resolving_placeholders(retry_transient_db(run_populate_media_details)),
            lane=DEFAULT_LANE,
        )
    )
    # Own serial lane: one pipeline job holds its slot for a whole channel
    # enumeration plus a per-video DB check, and a running job cannot be
    # preempted by priority. On the default lane the two cron job types could
    # occupy both slots and stall manual downloads.
    register_job(
        JobDefinition(
            name=SUBSCRIPTION_PIPELINE_JOB,
            fn=retry_transient_db(run_subscription_pipeline),
            lane=SUBSCRIPTIONS_LANE,
        )
    )
    register_job(
        JobDefinition(
            name=DIRECT_DOWNLOAD_PIPELINE_JOB,
            fn=run_direct_download_pipeline,
            lane=DEFAULT_LANE,
        )
    )
    register_job(
        JobDefinition(
            name=ADD_SUBSCRIPTION_JOB,
            fn=retry_transient_db(run_add_subscription),
            lane=DEFAULT_LANE,
        )
    )
    register_job(
        JobDefinition(
            name=CLEANUP_JOB,
            fn=retry_transient_db(run_cleanup_job),
            lane=DEFAULT_LANE,
        )
    )


def _fire_cron_job(job_name: str, args: tuple, cron_id: str) -> None:
    """Submit a cron-triggered job. Runs on the event loop (CronJob.fire).

    The fixed task_id makes ticks idempotent: if the previous identical job is
    still queued or running, the orchestrator ignores the resubmission.
    """
    from orchestrator import JobSpec, orch

    orch.submit_from_thread(JobSpec(job_name=job_name, args=args, task_id=cron_id, tracked=False))


def build_cron_jobs(subscription_minutes: int):
    from models import JobType
    from orchestrator.scheduler import CronJob, next_fire_daily, subscription_schedule

    subscription_schedule.set_minutes(subscription_minutes)
    jobs = [
        CronJob(
            name=f'subscriptions-{job_type.value.lower()}',
            compute_next=subscription_schedule.compute_next,
            schedule_token=lambda: subscription_schedule.version,
            fire=partial(
                _fire_cron_job,
                SUBSCRIPTION_PIPELINE_JOB,
                (job_type.value,),
                f'cron-subscriptions-{job_type.value.lower()}',
            ),
        )
        for job_type in (JobType.PLAYLIST_SUBSCRIPTION, JobType.CHANNEL_SUBSCRIPTION)
    ]
    jobs.append(
        CronJob(
            name='cleanup-temp-files',
            compute_next=partial(next_fire_daily, hour=3, minute=0),
            fire=partial(_fire_cron_job, CLEANUP_JOB, (), 'cron-cleanup-temp-files'),
        )
    )
    return jobs
