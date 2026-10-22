---
paths:
  - "backend/app/orchestrator/**/*"
  - "backend/app/tasks/**/*"
---

# Orchestrator and task-body internals

Deep detail for `backend/app/orchestrator/` and `backend/app/tasks/`. The lane list, priority ladder,
pipeline diagrams and feature summary live in the root `AGENTS.md`.

## `tasks/media.py` — the deferral rules

**`tasks/media.py` — the deferral rules.** Unreleased videos get a visible NOT_READY placeholder
TaskRecord (upserted per URL at populate time, soft-deleted and replaced by a QUEUED chain once the
video airs). "Unreleased" = live or upcoming premiere, or a finished livestream still flagged
`post_live` **whose VOD formats aren't available yet** — yt-dlp can keep `post_live` set for hours
or days after a downloadable VOD exists, so `is_video_ready_for_download` (`ytdlp/info.py`) allows
post-live once real formats are present.

**A deferral also persists a `NOT_READY` `MediaDetails` row** (`_defer_media`) carrying the expected
`release_timestamp` and a `next_check_at`, because the TaskRecord placeholder alone is invisible to
the subscription filter — which keys on `MediaDetails`, so without the row every premiere and every
unavailable video is re-fetched on *every* tick forever. `next_check_at` is a known future premiere
time, else an age-keyed ladder; `_evaluate_not_ready_media` honours it, except for direct downloads,
which always bypass the backoff. Two ladders: a short one for unreleased videos, a much longer one
for videos yt-dlp *positively* reports as gone. `get_url_info` swallows every failure into a `None`,
so `get_url_info_with_failure` + `classify_extraction_error` decide which — and the long ladder is
opt-in by whitelist, since a 429 or bot-check is otherwise indistinguishable from a private video and
parking a rate-limited channel for a week is the worse error.

**Three places must never accept a deferred row as resolved**, or an unreleased video downloads early
using its premiere metadata: the reuse branch and the pre-fetched-metadata shortcut in
`_use_pending_or_fetch_fresh` (both bypass the yt-dlp fetch, hence the readiness check —
`_reuse_or_delete_existing_media` blocks both at once), and the upsert on the way back out. Deferred
rows are updated in place, never delete-and-recreated like SKIPPED: the delete would reset
`created_at`, which the ladder reads. Clearing needs the explicit `sync_clear_deferral` because
`_copy_upsert_fields` refuses to write `status` when it is `NONE`.

## Resizing a lane

Three constraints on resizing:
- **A shrink is necessarily lazy.** Surplus permits are held by *running* jobs and reclaiming one
  would mean killing its job, so `set_concurrency` only retargets and the dispatcher retires permits
  via `absorb_surplus_permit` as jobs finish. `Lane.capacity` (in the `/tasks/runtime` snapshot) is
  what's actually in circulation and trails `concurrency` until then. When `absorb_surplus_permit`
  returns True the dispatcher must neither dispatch nor release — releasing would undo the shrink.
- **`set_lane_concurrency` is event-loop-thread only**, like every other queue mutation. The settings
  router is `async`, so it already is.
- **`downloads` above 1 changes what `download_sleep_seconds` means** — the sleep is per job body, so
  it stops pacing the deployment as a whole. `ml` above 1 runs a faster-whisper child per job, each
  sized by `transcription.whisper_cpu_threads`. Both are bounded to 1–8 server-side.

## Retargeting the cron cadence

**The cron cadence is retargeted live, and that needs `CronJob.schedule_token`.** `cron_loop`
recomputes a fire time only *after* firing, so a change from 60 to 5 would otherwise sit unapplied
until the top of the hour. `IntervalSchedule.version` bumps on a real change and
`refresh_stale_schedules` replaces the pending fire time — before the due-time check, so a retarget
displaces the pending fire rather than racing it. `next_fire_every_n_minutes` anchors its slots on
**midnight, not the hour**, which is what lets a value above 60 mean what it says; the hour-anchored
version silently collapsed everything above 60 to hourly.

## Subscription fan-out throttling

**The fan-out crosses a lane boundary, and that is the whole reason it needs throttling.** A lane
comes from the `JobDefinition`, not the submitting job (`JobSpec` has no `lane` field;
`_submit_nowait` resolves it via `get_job_definition`), so the pipeline holds the single
`subscriptions` slot while every `POPULATE_JOB` it emits lands on `DEFAULT_LANE`. A large channel
would otherwise park thousands of untracked, in-memory-only jobs there — a spike a restart silently
loses.

`_wait_for_fanout_capacity` (`tasks/subscriptions.py`) instead blocks the producer on
`orch.queued_count(DEFAULT_LANE)`, bounding peak depth without capping throughput (the lane still
drains at its own rate; we top it up). Safe to run long because `_fire_cron_job` submits with a fixed
`task_id` and `_submit_nowait` ignores a resubmit whose task_id is already queued or running — a long
pipeline suppresses its own overlapping ticks. `FANOUT_MAX_WAIT_SECONDS` bails if the default lane
wedges, losslessly: the next tick re-enumerates.

Throttle the producer, never the cycle. A pre-enumeration backlog guard that skips the whole tick
throws away a completed channel walk and stalls every *other* subscription for the duration of a
drain.

## The RESOLVING placeholder

Resolution is slow enough to look like a hang — a yt-dlp metadata fetch, a whole
playlist enumeration for `download_playlist`, and a wait behind `DEFAULT_LANE`'s
width — so a submission must be visible before populate finishes. `POST /ytdl/`
writes the download `TaskRecord` up front in `RESOLVING`, titled with the raw URL,
and the chain **adopts that row** rather than minting its own, so the task_id the
user is watching never changes.

Four things the code can't state:

- **The placeholder task_id must never be a `JobSpec.task_id`.** The chain is dispatched
  from *inside* the still-running populate job, so `_submit_nowait`'s idempotence guard
  (`core.py`) would see that id already in `_handles` and silently drop the download. It
  travels as payload data (`DownloadJobDTO.placeholder_task_id`), which is also why
  cancellation is cooperative — `orch.cancel` has nothing to dequeue, so `revoke_task`
  writes `CANCELLED` and `_adopt_placeholder` reads it and stands down.
- **`RESOLVING` is in `ix_task_records_active_unique`'s predicate but *not* in
  `ACTIVE_DOWNLOAD_STATUSES`.** The index gives double-submits the same DB-level dedup
  every other active status gets; staying out of the status list keeps
  `_find_duplicate_active_tasks` blind to the chain's *own* placeholder, which would
  otherwise make it stand down against itself.
- **`guard_resolving_placeholders` wraps *outside* `retry_transient_db`** (see
  `registry.py`). It is the guarantee that no submission is stranded in `RESOLVING` —
  every early return and raise lands in its `finally`, and `sync_retire_placeholder`
  no-ops unless the row is still `RESOLVING`. Inside the retry wrapper it would retire
  the row between attempts, and the retry would find nothing to adopt.
- **The guard is only for a body that owns the row to resolution.** `POPULATE_JOB` does —
  it adopts or writes a specific outcome before returning. `DIRECT_DOWNLOAD_PIPELINE_JOB`
  does *not*: it hands each row to a populate job and returns while that job is still
  queued, so a blanket retire-on-exit kills every chain it just started (silently — the
  chain stands down on the non-`RESOLVING` row and `SKIPPED` is not in the default Tasks
  filter). It is therefore registered unguarded and tracks a `unclaimed` set of task_ids
  in `run_direct_download_pipeline`, discarding each on hand-off and retiring the rest in
  a `finally`. Compare by id *value*: `expand_playlists_impl` round-trips every job
  through `serialize_download_job`, so the dict submitted is never the one that arrived.
  That `finally` is also why the retry moved *inside* the body, onto
  `_fan_out_download_chains` — the ordering rule above still applies.
- **Per-video placeholders are created *after* `filter_completed_downloads_impl`**, so a
  500-video playlist where 480 are already downloaded surfaces 20 rows, not 500 that
  instantly flip to SKIPPED. The rule: a placeholder exists for every URL the user
  explicitly named, plus every video that survives playlist filtering.

Startup recovery resumes these from `TaskRecord.pending_payload`, which is what keeps a
restart mid-resolution from losing the download with no trace: both the pipeline and
populate jobs are `tracked=False`, so the persisted payload is the only record of them.

## Lifecycle hooks (`orchestrator/hooks.py`, run by `orchestrator/wrapper.py`)

`BaseStatusHooks` (status writes + SSE) · `DownloadHooks` (live-row re-resolution, NOT_READY-preserving
success guard, downstream failure marking, cancel file cleanup) · `TranscriptHooks` / `ClipHooks` /
`SpriteHooks` (cleanup of partial transcripts / clip files / truncated sprite sheets `on_cancel`).

## Sprite generation

Runs on the `ml` lane as a tracked `SPRITE_GENERATION` task (`SPR` in the Tasks UI). The row is
created at **populate time** by `_persist_download_chain_state`, alongside the download and transcript
rows (VIDEO only), and dispatched later. **The visible SPR row is the point**: the transcript behind
it sits `QUEUED`, and without a row holding the slot that wait looked like a hang.

Four constraints the code can't state:
- **`queue_sequence` is the "dispatched yet?" marker.** The chain row is inserted with it NULL, which
  is what `crud.py`'s queue-position pass and startup recovery both key on — recovery must leave a
  QUEUED sprite alone while `_upstream_still_pending`, or it tiles a file that isn't on disk yet. The
  retry path re-arms a sprite row by clearing it back to NULL.
- **Dispatch lives in `DownloadHooks.on_success`, not the job body**, because that fires for exactly
  the right outcomes: normal success plus the repeat-download and file-already-exists paths (which
  have a file on disk), but not superseded/quota (`SkipJob`, no hooks) or not-ready (returns early on
  the NOT_READY retval). It is the last statement in the hook, so a raise — absorbed by
  `wrapper._run_hook` — costs only the sheet. Moving it back into the body loses all of that.
  Sprites still beat the transcript to the lane, since `core.py` enqueues `spec.downstream` only
  after `run_job_sync` returns.
- **Sprite rows set `download_job_url`**, so `ix_task_records_active_unique` dedups the automatic and
  manual paths for free. But that index counts `CANCELLED` as active, so a cancelled row must be
  retired before inserting (status → `DELETED`; setting `deleted_at` alone does **not** free the slot
  — the predicate has no `deleted_at` clause). Scope of a cancel: **within one chain it sticks** (the
  hook must not silently recreate the row, or the cancel button is a visible no-op), **a new download
  chain wipes the slate**, and **the manual Generate button always wins** (`revive_cancelled=True`).
- **ffmpeg cannot report progress on this pipeline.** `tile` buffers every frame and emits one packet
  at EOF, and `-progress`'s counters track the first output stream — the sheet. Measured on a 30-min
  video: one progress block, at the end; `split` + a second probe output (`null`, `rawvideo`,
  `mpegts`) all still gave one. Only a two-stage pipeline (frames to a temp dir, then tile) yields a
  real percentage. Hence the elapsed-time ticker via `run_ffmpeg_cancellable`'s `on_tick` — don't
  "fix" it by reaching for `-progress` again.

**`mark_downstream_stmt` sweeps every downstream task type by default**, which is what makes a
cancelled or failed download take its whole chain with it. The one exception is the pair of
download-skip paths (repeat download / file already exists): they must end the transcript *without*
touching the sibling sprite row that `on_success` is about to dispatch, hence
`sync_skip_downstream_transcripts` and the `task_types` filter. Both paths must write *something*:
`ctx.skip_downstream` only suppresses the in-memory enqueue, so a path that writes nothing strands
the transcript at `QUEUED` forever.

## The transcript-only chain

The root `AGENTS.md` has why `TRANSCRIPT` is a media type and the end-every-path rule. The parts the
code can't state:

- **`_run_transcript_only` (`tasks/downloads.py`) decides what to stage and nothing more.**
  1. Reuse anything an earlier attempt staged.
  2. Rate-limit sleep: caption fetches talk to YouTube like downloads do.
  3. A captions-only pass, skipped when `force_whisper_transcription` is on. It sets
     `ignore_no_formats_error`, since formats don't matter for captions and a video whose formats come
     back empty can still be indexed from them.
  4. Parse the caption file (pure Python) only to decide whether audio is needed. An unusable file is
     *deleted*, or the transcript job would pick it up in place of the Whisper audio.
  5. Anything short of usable captions downloads the smallest *English* stream
     (`_TRANSCRIPT_ONLY_AUDIO`, `ytdlp/options.py`) with no re-encode, since Whisper resamples
     anyway. `YtPostProcessor` records it as `file_path`, which is what recovery reads. **Never
     `worstaudio`**: it inverts the whole format sort, language preference included, so on a
     video carrying YouTube auto-dubs it picks a dub, and `tiny.en` renders that as English-looking
     noise. `lang` must also lead `format_sort`, since `+size` alone still lands on whichever dub
     encodes smallest. HLS comes first within each tier because on some videos YouTube 403s every
     https audio stream partway through while the HLS one completes — `worstaudio` only ever
     worked because it happened to land on HLS.

  A subtitle-fetch `DownloadError` goes to Whisper, not to `ctx.retry`.
- **Staged files are named by URL hash alone**, so `cleanup_task_files`, which matches
  `'{title} [{hash}]'`, never finds them. `DownloadHooks.on_failure`/`on_cancel` delete them
  explicitly for TRANSCRIPT jobs. `on_success` writes `POSTPROCESSING`, never `COMPLETE`, and
  dispatches no sprite.
- **`TranscriptHooks` end the item.**
  - `on_success` → `COMPLETE` (stamping `downloaded_at`, which the Transcripts view groups as "Added").
  - `on_failure` → `FAILED`; `on_cancel` → `CANCELLED`.
  - Each deletes the staging; `on_retry` keeps it.

  `sync_finish_transcript_only` always clears `file_path` but moves `status` only off an in-flight
  one (the `_CANCELLABLE_MEDIA_STATUSES` guard), so a late hook can't knock a `COMPLETE` row back.
- **The thumbnail is staged too, and kept only by `on_success`.** The download job saves YouTube's
  480px `hqdefault` as `{staging}.thumb.jpg`; `_finish_transcript_only` moves it to
  `.thumbnails/` **before** deleting the staging, which would otherwise take it, and writes its
  path in the same UPDATE. Every other ending is then covered by the staging cleanup it already has.
  The column is only ever *set* there, so a late failure hook can't clear a kept thumbnail.
- **A queued transcript cancelled by the user runs no hook at all.** So `revoke_task` and the bulk
  cancel call `finish_transcript_only` for either half of a TRANSCRIPT chain. Skip it and the row sits in
  `POSTPROCESSING` forever: the subscription filter skips `POSTPROCESSING`, and the sweep keeps the
  files of anything in flight.
- **Retrying a TR row that has nothing staged re-runs its download.** That happens after a terminal
  failure or a cancel, both of which deleted the staging. The download row is `COMPLETE`, which the
  ordinary retry refuses, so `_retry_transcript_only_from_download` re-arms it directly, with the TR
  chained behind.
- **`create_transcript_blocks` raises when a TRANSCRIPT row has neither captions nor a file**, rather
  than producing an empty transcript that would mark the item done.
- **The staging sweep (`cleanup_orphaned_transcript_staging`) keeps an in-flight item's files
  regardless of age.** A Whisper backlog on the serial ml lane can legitimately outlast
  `cleanup_age_hours`.

## Clips from the source

An item with no file (transcript-only, or deleted media that kept its transcript) is clipped from
YouTube by `CLIP_FROM_SOURCE_JOB` (`run_source_clip_job`, `tasks/clips.py`). The router picks it
whenever `has_playable_file` is false, never on a bare `file_path` check: a transcript-only row
mid-chain carries its *staged Whisper audio* as `file_path`, which is neither the media nor kept.

- **yt-dlp only resolves the streams; the app's own ffmpeg fetches and cuts the section.** yt-dlp's
  `download_ranges` runs ffmpeg under a blocking `proc.wait()` with no progress hooks, so a cancel
  could not land until the fetch finished, and a precise cut there needs `force_keyframes_at_cuts`,
  which encodes to the container's defaults, so an MP3 takes a second lossy pass. Through
  `run_ffmpeg_cancellable` a cancel kills the process group like any clip's, and the output is
  encoded once, exactly as a clip of a local file is.
- **ffmpeg must be handed what yt-dlp would send.** `_source_clip_inputs` copies `FFmpegFD`
  (`yt_dlp/downloader/external.py`): each stream's `http_headers` as `-headers`, and the session's
  cookies for its URL as `-cookies`. The command is therefore never logged — it holds signed URLs
  and cookies.
- **ffmpeg must also wait as yt-dlp would.** YouTube holds a session's streams back for the length
  of the pre-roll ad it would have shown, and 403s any fetch before then. yt-dlp records the moment
  as each format's `available_at` and its downloader sleeps until it, which is why its own range
  download works where a fetch straight after `extract_info` fails. `_wait_until_streams_served`
  copies the sleep on the job's cancel event. Measured 2026-10-21: a ~3s wait, and fetches of
  `web_embedded` streams made before it 403'd.
- **It runs on the downloads lane**, paced with the downloads, since it talks to YouTube. On `ml`
  it would wait behind Whisper. It is still a `CLIP_GENERATION` task with `ClipHooks`, so recovery
  and the retry ceiling treat it as any clip.
- **Its options carry `sleep_interval_requests` but never `ratelimit`**, the same split as
  metadata extraction: yt-dlp transfers nothing. The format selectors exclude
  `http_dash_segments`, a manifest only yt-dlp's own downloader can assemble.
