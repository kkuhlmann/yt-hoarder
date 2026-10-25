# AGENTS.md

Guidance for AI coding agents working in this repository.

`README.md` covers what the app does and how an end user installs it — read that for the feature
tour. This file covers what you can't get by reading the code: invariants, traps, and the reasons
behind non-obvious choices.

## Deep-dive files

Area-specific detail lives in `.claude/rules/`. Each file carries `paths:` frontmatter, so Claude
Code pulls it into context automatically when you open a matching file; other agents should read the
one covering the area they're about to touch.

| Read when touching | File | Covers |
|---|---|---|
| `app/orchestrator/`, `app/tasks/` | `.claude/rules/orchestrator.md` | Deferral rules, lane resizing, cron retargeting, fan-out throttling, the RESOLVING placeholder, lifecycle hooks, sprite generation, the transcript-only chain, clips cut from YouTube |
| `app/repositories/`, `app/routers/`, `app/services/`, `models.py`, `migrations/` | `.claude/rules/backend-domains.md` | The access-repo factory, `/semantic/search` scoping, embeddings, password recovery, migrations, the seeded `app_settings` row, transcript-only media in queries |
| `frontend/` | `.claude/rules/frontend.md` | Next 16 / Turbopack / Tailwind v4 toolchain, component and hook constraints, `useAudioAnalyser`, the media page and transcript panel, the YouTube embed, Vitest |
| Dockerfiles, compose files, workflows, `Taskfile.yml`, `setup.sh`, `config.py` | `.claude/rules/deployment.md` | Taskfile container traps, multi-arch images and GHCR, config plumbing |
| `backend/tests/` | `.claude/rules/testing.md` | The conftest fixture chain, and why each piece of it is load-bearing |
| `app/ytdlp/` | `.claude/rules/ytdlp.md` | Player clients, the three throttling knobs, POT provider keys, cookie handling, impersonation |
| `app/mcp_server/`, `repositories/access_tokens.py`, `routers/tokens.py` | `.claude/rules/mcp.md` | Why the MCP transport must be stateless, Route not Mount, its own bearer auth, the token validity rules, the logging restore, in-app link base URLs |

**New guidance goes in whichever file matches its scope** — cross-cutting here, area-specific in the
matching rule file. Every rule file must keep its `paths:` frontmatter: without it the file loads
into *every* session, which is the problem this split exists to solve.

## Architecture

### Backend (`backend/app/`)

`database.py` keeps **dual async/sync engines**: async for FastAPI and the orchestrator control
plane, sync for job bodies running in lane threads and in the ML child process.

**Orchestrator** (`orchestrator/`) — in-process engine for all background work. The parts you wouldn't
get from the filenames:
- `lanes.py` - pop order `(priority, queue_sequence)`, ties by submission order
- `wrapper.py` - `run_job_sync`: before_start → body → on_success/on_cancel/on_retry/on_failure, NOT_READY guard, never-overwrite-CANCELLED guard, downstream chaining
- `recovery.py` - startup recovery rebuilds lanes from TaskRecord truth, since Postgres is the only durable task state; plus the due-retry loop
- `subprocess_runner.py` / `child_main.py` - spawned child for transcription; faster-whisper/ctranslate2 load **only there**, so it's SIGTERM-cancellable and memory is reclaimed per job

**Tasks** (`tasks/`) — job bodies are plain functions taking `(ctx, payload)`; `registry.py` wires
bodies/lanes/hooks/policies via `register_all_jobs()`.

`__init__.py` re-exports job bodies and deliberately does **not** import `tasks.transcription` —
that keeps faster-whisper out of the main process.

**Models** (`models.py`) — the constraints worth knowing before you write a query:
- `Tag` unique per `(user_id, name)`; `MediaTag` unique per `(user_id, media_details_id, tag_id)`
- `MediaRating` one per `(user_id, media_details_id)`, 1–5 enforced by a CHECK constraint
- `MediaAccess` carries a source type: DIRECT, PLAYLIST, SUBSCRIPTION
- `MediaType` has a third value, `TRANSCRIPT`, for media indexed but never kept (see *Transcript-only media*); `LIBRARY_MEDIA_TYPES` is the other two
- `Subscription` unique per `(url, string_match, media_type, user_id)` — `media_type`, not `audio_only`, since a transcript-only subscription is `audio_only` without being an audio one. Editing `audio_only` moves `media_type` with it; a TRANSCRIPT subscription's mode is fixed
- `User` also holds the recovery columns: `password_reset_requested_at`, `must_change_password`, `password_changed_at`, `recovery_code_hash`, `recovery_code_expires_at`

**Repositories** (`repositories/`) — `task_records/` is a package: `crud.py` (async), `retry.py`
(downstream marking + retry/dispatch), `sync_ops.py` (sync, for job bodies), `bulk.py`; its
`__init__.py` re-exports the externally-used names so `from repositories import task_records` keeps
working. `base_access.py` is a factory generating share/unshare/has_access for three structurally
identical access repos — **`media_access.py` is hand-written and deliberately cannot join them**.

**Routers** (`routers/`) — one per domain, named for the prefix they mount. Two things you wouldn't
guess: `/media-details` also owns **tags and ratings** (they have no router of their own), and its
keyword `search` supports `&&` / `||` operators (parsed in `_build_search_condition`,
`repositories/media_details.py` — `&&` binds tighter than `||`, and single `&` / `|` are literal).
`/health` is inline in `main.py`, as is the SPA catch-all, which **404s any unmatched path under
`/api/` or `.well-known/`** instead of falling through to `index.html`. Without that guard an unknown
endpoint answers 200 with a page of HTML, which no client can tell from a real response — the service
worker deciding whether a reply is cacheable least of all. `resolve_spa_file` returns `None` for those paths;
everything else still falls back to the shell so client-side routes keep booting. The MCP endpoint
(`{API_PREFIX}/mcp`, `mcp_server/http.py`) is registered there too, as two exact routes ahead of the
catch-all; personal access tokens for it are managed under `/auth/tokens`.

### Frontend (`frontend/app/`)

Next 16 (Turbopack, flat ESLint config, Tailwind v4 CSS-first) statically exported in production.
**There are two hook directories**: `hooks/useTaskProgress.ts` (SSE) lives in `app/hooks/`,
everything else in `app/_hooks/`. **Offline mode** adds a service worker built out of `frontend/sw/`
by a `postbuild` step; it is type-checked separately against `lib: WebWorker`, so the modules it
shares with the app must stay free of `window`/`document` — see `.claude/rules/frontend.md`. It only
registers over HTTPS, which is why `docs/CONFIGURATION.md` carries a TLS section. **Offline mode is
playback-only**: every server-touching control is hidden behind `useOffline()` while it is on, and
playlists come from a membership snapshot (`GET /playlists?include_media_ids=true`) — the gates and
the snapshot's semantics are in that rule file too. The toolchain carries several constraints that are easy to undo by
accident — Tailwind's `@theme inline` block, the non-monotonic radius scale, `@source` paths,
`allowedDevOrigins` — all in `.claude/rules/frontend.md`.

## Authentication & Access Control

JWT via HTTP-only cookies. First registered user is auto-admin and auto-approved; the rest need admin
approval. `middleware/auth.py` sets `request.state.user_id` / `is_admin` on every request;
`dependencies.py` enforces optional / required / admin.

**Access control is three-tier:** Owner (`entity.user_id`) → Shared (AccessTable row) → Admin. The
filter is `effective_user_id = None if (admin_view and is_admin) else user_id`, where None means no
filter at all. A shared user's "delete" removes only their own access row; the entity persists for the
owner. Tags and ratings are **per-user, not per-entity** — `Tag`, `MediaTag` and `MediaRating` all
carry `user_id`, so two users sharing a media row keep independent tags and ratings on it.

**Personal access tokens authenticate the MCP endpoint and nothing else**, and the cookie never
authenticates MCP: the REST middleware reads only the cookie, the MCP endpoint only the `Bearer`
header. A token acts as its user with the same Owner → Shared scope as the UI (never admin view), and
dies with a password change by the same `password_changed_at` rule as cookie sessions. Details in
`.claude/rules/mcp.md`.

**Storage limits:** `User.storage_limit_bytes` (nullable, None = unlimited), set via
`PUT /auth/users/{user_id}/storage-limit`, measured from actual files owned by the user.
Transcript-only items are exempt at all three checks (router, populate, download job): they keep no file.

**Password Recovery** — no email integration exists; every path is trust- or filesystem-based
(`/auth/forgot-password`, admin recovery via a single-use code in `/data/admin-recovery.txt`, and the
`task admin:reset-password` CLI). Session invalidation is the load-bearing mechanism: the middleware
drops any token whose `iat` predates `User.password_changed_at`. The invariants — including which
paths set `must_change_password` and why there is no opt-out — are in
`.claude/rules/backend-domains.md`.

**Cancel must write a terminal `MediaDetails.status`** (`mark_download_cancelled` /
`sync_mark_download_cancelled`), from all three cancel paths: `revoke_task`, `bulk_cancel_tasks`, and
`DownloadHooks.on_cancel`. It was the only lifecycle path that didn't — `before_start`/`on_success`/
`on_failure` all write one — so a queued-then-cancelled download kept populate's `NONE`. `NONE` is
absent from `_FILTER_SKIP_STATUSES`, so every later subscription tick re-included the URL and spawned
a populate job that could never produce a download (the CANCELLED `TaskRecord` blocks task creation
via `ACTIVE_DOWNLOAD_STATUSES`) — a permanent per-tick loop growing with every cancellation. Only
in-flight statuses are overwritten, so cancelling a *transcript* task can't disown a finished download.

**`POST /ytdl/` pre-checks `_URL_BLOCKING_STATUSES` and 409s**, rather than letting the pipeline
discover the conflict. That list mirrors every status which makes `_find_duplicate_active_tasks` stand
down, so a submission that gets past it is guaranteed a chain. `CANCELLED` is checked *before* the
ownership split — a cancel holds the URL's slot against whoever asks next, not just the user who
cancelled it — and gets its own message, since the fix is to retry or delete that task rather than to
resubmit. Without this, re-requesting a cancelled URL returned 201 and then did nothing at all.

## Task Orchestrator Architecture

All background work runs inside the uvicorn process via `orchestrator/`. Postgres `task_records` is
the only durable task state. Job registration lives in `tasks/registry.py` (`register_all_jobs()`),
called from the lifespan.

### Lanes

Four lanes (`lanes.py`), each 1 wide by default except `default` at 2. **Widths live in
`app_settings` (one column per lane) and are edited live in the Settings UI**, not in `config.yml` —
the lifespan seeds `orch.start()` from the row via `settings_repo.lane_concurrency`, and every
`/settings` write path re-applies the whole mapping through `orch.set_lane_concurrency`.
`orchestrator/jobs.py`'s `DEFAULT_LANE_CONCURRENCY` is only the no-database fallback; a test pins it
to the model defaults so the two can't drift.

Resizing a lane and retargeting the cron cadence both have constraints — see
`.claude/rules/orchestrator.md`.

**Why the subscription pipeline has its own lane:** one pipeline job holds its slot for an entire
channel enumeration plus a DB check per video (minutes for a large channel), and priority orders the
*queue* — it cannot preempt a running job. On the `default` lane the two cron job types could occupy
both slots at once and stall manual downloads regardless of priority.

**Priority ladder** (`JobSpec.priority`, 0 = highest; the lane pops by `(priority, queue_sequence)`,
ties by submission order): `0` = explicit Prioritize button, add-subscription and a subscription's
Check now, `1` = `DIRECT_DOWNLOAD_PRIORITY` (manually-submitted downloads, and what a Check now
finds), `5` = `SUBSCRIPTION_DOWNLOAD_PRIORITY` and the `JobSpec` default. Constants live in `repositories/task_records/crud.py`. **Priority is not
inherited** — `JobContext` doesn't carry it and the orchestrator copies only `args` into downstream
specs, so every fan-out site sets it explicitly. Consequence: a subscription backlog can be starved by
a steady stream of manual downloads. That is intended (explicit user requests beat background sync)
and lossless — a populate that never runs leaves no `MediaDetails`, so the next enumeration
rediscovers the video.

### Subscription pipeline

Fired by the built-in cron every `app_settings.subscription_check_minutes` (Settings UI, 1–1440):

```
run_subscription_pipeline (subscriptions lane, serial; plain control flow, early return ends the tick)
├─ get_all_subscriptions_impl
├─ create_download_jobs_from_subs_impl
├─ filter_completed_downloads_impl   (batched: one url IN (...) query per media_type)
└─ fan out → populate_media_details jobs (default lane, parallel, priority 5)
       throttled: blocks while the default lane holds ≥ FANOUT_QUEUE_TARGET
```

The fan-out crosses a lane boundary, which is why it throttles the producer rather than the cycle
(`.claude/rules/orchestrator.md`).

**Check now** (`POST /subscriptions/{id}/check`, owner or admin) runs the same steps for one
subscription as `SUBSCRIPTION_CHECK_JOB` (`run_subscription_check`), on the same lane, at priority 0
and **whether or not the subscription is enabled**. What it finds fans out at priority 1, and
`DownloadJobDTO.priority` carries that through populate to the download chain, which otherwise ranks
by `subscription_id`. Its fixed `task_id`, `subscription-check-{id}`, collapses repeat presses into one
channel walk; it is submitted under the *owner's* `user_id`, since `GET /tasks/{id}` lets only that
user (or an admin) poll an untracked job.

### Download chain (per job)

```
POST /ytdl/ writes a RESOLVING placeholder TaskRecord, then submits the pipeline
run_populate_media_details (resolves URL, fetches metadata)
└─ create_download_and_transcript_chains_impl
   ├─ adopts the placeholder as the download row (or inserts one), then dispatch_download_chain →
   ├─ download job (downloads lane)
   └─ (optional) transcript JobSpec attached as `downstream` — enqueued into the
      ml lane with the download's return value once the download succeeds
```

`POST /ytdl/` writes that placeholder before resolution starts so a slow submission is still visible.
Its four invariants — and the reason the task_id must never be a `JobSpec.task_id` — are in
`.claude/rules/orchestrator.md`.

### Features

- **UUID task IDs** pre-assigned before queueing (`TaskRecord.task_id`); ordering via Postgres sequence `task_queue_sequence`
- **Duplicate detection** — partial unique indexes prevent concurrent downloads of the same URL/media_type
- **Retries** — `RETRY` status + `next_retry_at` scanned by the retry scheduler; exponential backoff (downloads 300s–8h ×20), same task_id across attempts (`retry_count` = attempt number, drives cookies-on-retry)
- **Startup recovery** — re-enqueues QUEUED work, resumes interrupted downloads (yt-dlp continues `.part` files) and re-runs the populate job behind a `RESOLVING` placeholder; `tasks.purge_on_startup: true` cancels pending work instead
- **Cancellation** — queued jobs dequeue instantly; running downloads abort at the next yt-dlp progress tick via a cancel event; the transcription child is SIGTERM'd; clip and sprite ffmpeg get their process group killed; a `RESOLVING` row is cancelled cooperatively (status only — no job exists under its id yet)
- **Prioritize** reorders the in-memory queue entry to the front, task_id unchanged
- **Phase tracking** distinguishes VIDEO vs AUDIO download phases; rate limiting sleeps between downloads (cancel-event aware)
- **Observability** — `GET /tasks/runtime` (admin) or `task tasks:runtime` shows lanes + queued/running jobs

To clear pending work: cancel from the Tasks UI, set `tasks.purge_on_startup: true`, or `task clean`.

Lifecycle hooks (`orchestrator/hooks.py`) and sprite generation are covered in
`.claude/rules/orchestrator.md`.

## Transcription Pipeline

`services/transcript.py` chunks audio through ffmpeg rather than loading it whole, and extracts at
**32 kbps / 16 kHz mono** — Whisper's expected input, so anything richer is wasted bytes.

**Captions are best-effort and must never fail a download.** A video with no `en` track just logs
and carries on, but a *listed* track whose fetch fails (a 429 or 403 from the caption endpoint)
makes yt-dlp's `_write_subtitles` raise `DownloadError` before any media moves. Left alone that
retried the whole download to FAILED and the transcript never reached Whisper, so
`_download_with_fallback` (`tasks/downloads.py`) re-runs once with `with_subtitles=False` and the
transcript job falls through to Whisper. Don't reach for `ignoreerrors=True` instead: it swallows
real media errors too, and a download that fetched nothing would return as a success.

## Transcript-only media

A `MediaType.TRANSCRIPT` item is indexed for search but never kept as media: its row ends `COMPLETE`
with transcript blocks and no file. **It is a media type rather than a flag on purpose.** Every dedup
key — `uq_media_details_url_type`, `ix_task_records_active_unique`, the subscription filter's
`(url, media_type)` lookup — already includes `media_type`, so a transcript-only row can never make a
later real AUDIO/VIDEO download of the same URL look done, and two users indexing one channel share
one row through the ordinary cross-user grant. The library lists `LIBRARY_MEDIA_TYPES`; transcript
search and MCP span every type.

The chain is the ordinary DOWNLOAD → TRANSCRIPT pair. The download job only **stages** — YouTube
captions, else the smallest *English* audio stream for Whisper — under `{audio_path}/.transcript-only/{url_hash}.*`,
and never runs ML; the transcript job embeds on the ml lane like any other. Captions are found by that
URL-derived name, not a payload path, because startup recovery rebuilds the transcript job from the
database row alone. The row sits in `POSTPROCESSING` until the transcript lands, and **every way the
chain can end must delete the staging and give the row a terminal status**: the hooks cover jobs that
ran, and the cancel endpoints cover a transcript cancelled while still queued, which no hook ever
sees. Details in `.claude/rules/orchestrator.md`.

**A thumbnail lives exactly as long as the media file or the transcript does.** A transcript-only
item's (YouTube's 480px image, ~20 KB) is staged with its captions and kept only once the
transcript lands, so a chain that fails keeps none; a library item deleted with its transcript kept
keeps its thumbnail, until that transcript goes too. Kept ones live in `{audio_path}/.thumbnails/`;
the delete paths are in `.claude/rules/backend-domains.md`.

**A full download supersedes the transcript-only entry at query time, per viewer**:
`_build_media_conditions` hides a TRANSCRIPT row whenever the user can see a full row of the same URL
that has transcript blocks. Not by deleting access rows — the next subscription tick would re-grant
them, and an owner can't lose `owner_id` — which is also why the subscription filter drops such jobs
*without* the cross-user grant it makes for everything else. Details in
`.claude/rules/backend-domains.md`; the frontend's single media page (no modal) is in
`.claude/rules/frontend.md`.

## yt-dlp Configuration

Browser impersonation and player-client settings avoid YouTube blocking. The three facts that matter
from outside `ytdlp/`:
- **Every yt-dlp version bump goes through the `upgrading-yt-dlp` skill** — a hand edit of the floor in `backend/pyproject.toml`, a `uv lock --upgrade-package yt-dlp`, or a Dependabot PR moving it in `backend/uv.lock` — before the lock is committed or the PR merged. `player_client` is passed explicitly, so upstream's default-client changes never take effect on their own; the skill reads the release notes, the YouTube PRs' comment threads and a diff of yt-dlp's client table to decide what the app has to change with it
- **Challenge solving**: yt-dlp ≥2025.11.12 needs an external JS runtime for YouTube `sig`/`n` challenges — **Deno ≥2.3.0** (auto-discovered from PATH) plus the `yt-dlp-ejs` dep. Deno is pinned via the `DENO_VERSION` ARG in `Dockerfile.prod`; too-old Deno → `Signature solving failed` → `Requested format is not available`
- Defaults live in `models.py` (`DEFAULT_PLAYER_CLIENTS` / `DEFAULT_COOKIES_PLAYER_CLIENTS`) and **only affect a fresh `app_settings` row** — existing installs keep stored values, so change them via the Settings UI, or ship a data migration as `realign_player_clients` does. Watch for clients yt-dlp removes upstream (e.g. `tv_embedded`) *and* for ones it keeps but demotes: `android_vr` still exists, but YouTube 403s every format it returns as of 2026-08-17, so a stored list still leading with it silently caps downloads at whatever the next client offers

Throttling knobs, POT provider keys, cookie handling and impersonation pinning are in
`.claude/rules/ytdlp.md`. For a download failing *right now* — empty files, 403s, `Signature solving
failed`, a video stuck as not-yet-released — use the `yt-dlp-diagnostics` skill.

## Development Commands

`bash setup.sh` does interactive first-time setup, defaulting to the published GHCR image. Day to day
this project uses [Taskfile](https://taskfile.dev/) — run **`task help`** for the target list rather
than looking for it here.

**Three runnable compose files, one shared base.** `docker-compose.common.yml` holds `postgres` and
`backend-common`, plus `backend-prod-common` — which extends `backend-common` via a *same-file*
`extends` (no `file:` key, resolved against common.yml rather than whichever file `-f` named) and
carries the prod runtime: the `command:`, `stop_grace_period`, and the 60s healthcheck `start_period`.
`docker-compose.prod.yml` and `docker-compose.published.yml` each extend that and add only where the
image comes from. The chain exists so the released stack cannot boot differently from the one
contributors test — moving `command:` back into either leaf reintroduces exactly that drift.

Adding a Taskfile target that touches a container has three traps — see
`.claude/rules/deployment.md`.

## Testing

Backend tests run on the **host** (`cd backend && uv run pytest`, or `task backend:test`) against a
session-scoped `pgvector/pgvector:pg17` testcontainer, so Docker must be up. pytest-asyncio is in `auto`
mode: a plain `async def` test just works — **don't add `@pytest.mark.anyio`**, it is redundant here
and only re-parametrizes the test id.

Frontend tests run via **Vitest** — `task frontend:test`, or `npm test` from `frontend/`. Tests
are **co-located** next to their source as `*.test.ts` / `*.test.tsx`.

Tests needing real ffmpeg/ffprobe carry `@requires_ffmpeg` (`tests/test_peaks.py`) and auto-skip on
hosts without it; CI installs ffmpeg and runs them.

The `tests/conftest.py` fixture chain is built for speed and every piece of it is load-bearing — read
`.claude/rules/testing.md` before changing it. Frontend test conventions (node vs jsdom, globals off,
explicit `cleanup()`) are in `.claude/rules/frontend.md`.

## Configuration

`config.yml` is the single source of settings — see `config.sample.yml` for the full annotated set,
and `.env.sample` for compose mounts and build args.

**Priority is `config.yml` > env var > default, which is the opposite of what you'd expect.**
Double-underscore env vars (`DATABASE__URL`,
`TASKS__PURGE_ON_STARTUP`) fill in only what `config.yml` leaves *unset*:
`_create_settings_from_yaml` passes each YAML section as constructor kwargs, and pydantic-settings
ranks `init_settings` above `env_settings`. `test_yaml_values_take_precedence_over_env`
(`tests/test_config.py`) pins this. Don't "fix" it without deciding to flip the order deliberately —
several call sites assume config.yml is authoritative.

`config.py` loads it with `pydantic-settings` + custom YAML loading, cached via `@lru_cache` at
startup, nested Pydantic models for validation. Import with `from config import settings`.

**Lane widths and the subscription cadence live in `app_settings`, never in `config.yml`.** Don't add `tasks.default_concurrency` or `tasks.schedule_frequency_minutes` to `setup.sh` or `config.sample.yml`: `TasksSettings` has neither field and `extra='ignore'` swallows both, so a config.yml carrying them is silently inert rather than an error.

**Runtime settings** live in the single-row `app_settings` table (see the `AppSettings` model) and are
edited in the Settings UI, taking effect on the next task execution without a restart. **The row is
INSERTed by `baseline_schema.py`, so the model's field defaults never execute for this table** — what
that means for adding or changing a column is in `.claude/rules/backend-domains.md`.

Env-var reachability under Docker, the `.env` values that bite, and the pinned storage paths are in
`.claude/rules/deployment.md`.

## Code Style

- **Default to no comment. Fewer, higher-value comments beat more of them.** Before writing one, ask
  whether the code can say it via naming/structure instead — if so, don't add it. A comment earns its
  place only when it captures something the code can't: a non-obvious constraint, an invariant, a
  workaround, or a reason a "cleaner" alternative would be wrong. This cuts both ways: don't add
  low-value comments, and delete existing ones that don't clear the bar when you're already touching
  that code.
  - Bad — restates the line below it: `# Return the final tags` above a tag-select query, or
    `# Initialize SQLAlchemy database engines` above `db.initialize_database()`. Delete on sight.
  - Good — states a constraint the code can't: `# Race condition: another worker already created a task
    for this URL. The partial unique index (ix_task_records_active_unique) prevents duplicates.`
    Worth keeping.
- **Don't narrate history** ("this used to do X", "previously we..."). That belongs in the commit message,
  not the code, and it rots as the codebase moves on. The one exception: when the past behavior is the
  only thing that makes a present-day invariant make sense — and even then, keep the present-day
  constraint as the point, with the history as one clause in service of it, not a changelog entry. (E.g.
  explaining a cache is keyed on a value's *contents* because that value used to be a fresh object
  literal each render — enough to stop someone from "simplifying" it back to an identity check.)
- **Docstrings get a different bar than inline comments, not a free pass.** Their audience is a caller who
  won't read the implementation, so describing parameters/return shape/side effects is their actual job —
  that's not a WHAT-violation. But a docstring that only restates the function name in prose (e.g.
  `"""Get all subscriptions for a user."""` above `get_all_subscriptions_impl(user_id)`) adds nothing a
  signature didn't already say, and should go. FastAPI route docstrings are a further special case: they
  render in the generated OpenAPI/Swagger UI as end-user-facing API documentation, so judge them as
  documentation for API consumers, not narration for code readers.
- Backend: Ruff formatter, single quotes, 100 char lines. Frontend: ESLint flat config, TypeScript strict.
- **React Compiler rules — 10 are deliberately held at `warn`, and that list is a regression net, not a backlog.** `eslint-config-next@16` pulls in `eslint-plugin-react-hooks@7`, which turns the whole React Compiler rule set on as errors; that was 78 findings on arrival, so `reactCompilerRules` in `eslint.config.mjs` downgraded them to be worked off rule by rule. Every rule that ever had a finding — `purity`, `immutability`, `refs`, `set-state-in-effect`, `set-state-in-render` — is now clear and back at `error`. The 10 still listed have **zero** findings in the current tree and stay at `warn` only so a future violation surfaces as a warning to triage rather than an immediate hard build break. **This is intentional, not a broken config** — anything absent from that array keeps the default `error`, so removing a rule from it is how a rule gets promoted. Lint must stay at **0 errors**.
- **Query-building convention** (repositories):
  - **Conditions list** for queries with optional/conditional filters: `conditions = []` → append → `stmt.where(and_(*conditions))`
  - **Inline `.where()`** for fixed-condition queries: `select(Model).where(Model.id == id)`
  - Avoid chaining conditional `.where()` calls; use the conditions list pattern instead

## README Maintenance

`README.md` targets **end users**; this file targets **developers and agents**. When a change touches
user-facing behavior, config options, architecture, compose setup, dev commands, or troubleshooting,
check whether the README needs the same update.
