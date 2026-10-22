# Job bodies for the orchestrator, re-exported for convenient imports.
# Registration with the orchestrator happens once at app startup via
# register_all_jobs() (see tasks/registry.py).
#
# NOTE: tasks.transcription is deliberately NOT imported here — it is resolved
# lazily inside the spawned ML child (orchestrator.child_main), keeping
# faster-whisper out of the main process entirely.

from tasks.clips import run_clip_job, run_source_clip_job
from tasks.downloads import run_download_job
from tasks.media import (
    create_download_and_transcript_chains_impl,
    filter_completed_downloads_impl,
    populate_media_details_impl,
    run_populate_media_details,
)
from tasks.registry import register_all_jobs
from tasks.scheduling import (
    cleanup_temp_files_impl,
    expand_playlists_impl,
    run_cleanup_job,
    run_direct_download_pipeline,
)
from tasks.sprites import run_sprites_job
from tasks.subscriptions import (
    add_subscription_details_impl,
    create_download_jobs_from_subs_impl,
    get_all_subscriptions_impl,
    run_add_subscription,
    run_subscription_pipeline,
)

__all__ = [
    'add_subscription_details_impl',
    'cleanup_temp_files_impl',
    'create_download_and_transcript_chains_impl',
    'create_download_jobs_from_subs_impl',
    'expand_playlists_impl',
    'filter_completed_downloads_impl',
    'get_all_subscriptions_impl',
    'populate_media_details_impl',
    'register_all_jobs',
    'run_add_subscription',
    'run_cleanup_job',
    'run_clip_job',
    'run_direct_download_pipeline',
    'run_download_job',
    'run_populate_media_details',
    'run_source_clip_job',
    'run_sprites_job',
    'run_subscription_pipeline',
]
