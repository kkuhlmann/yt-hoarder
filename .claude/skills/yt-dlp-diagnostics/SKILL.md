---
name: diagnosing-yt-dlp-download-failures
description: >-
  Use when a yt-dlp download in yt-hoarder fails, comes back empty/zero-byte, or throws
  "Signature solving failed", "Requested format is not available", "unable to download video
  data: HTTP Error 403", repeated HTTP 403s, or rate-limit-like blocking — before assuming it's a
  player-client misconfiguration OR reaching for the throttling settings. Also use when a video
  is stuck/deferred as "not released yet" / "still processing after live stream" (post-live)
  though it looks available — a release-detection gate, not a download failure.
---

# Diagnosing yt-dlp Download Failures

## Overview
yt-hoarder wraps yt-dlp; downloads can fail for several independent reasons — a missing/stale Deno JS runtime, a stale yt-dlp/yt-dlp-ejs pin, rate limiting, or player-client selection. Player-client tweaking is only one possible fix and is usually NOT the root cause — check cheaper causes first.

**Establish the PHASE before anything else.** It splits the causes cleanly and the two halves have almost nothing in common:

| Phase | Looks like | Cause space |
|---|---|---|
| **Extraction** | 429, bot-check, `Sign in to confirm`, `Signature solving failed`, `Requested format is not available`, no formats found | Deno/yt-dlp/ejs, rate limiting, player-client, cookies |
| **Transfer** | Extraction clean, format selected, then `unable to download video data: HTTP Error 403` | See [Download-phase 403](#download-phase-403-transfer-fails-after-extraction-succeeds) — **not** the throttle knobs |

The app logs `Download <task_id>: attempt=N cookies=… clients=[…]` (`backend/app/tasks/downloads.py:734`) immediately before the transfer starts. An error *after* that line is transfer-phase.

## When to Use
- Downloaded file is empty / zero bytes
- Error: `Signature solving failed`
- Error: `Requested format is not available` (often appears right after the above)
- Error: `unable to download video data: HTTP Error 403: Forbidden` (transfer-phase — see below)
- Many downloads across different videos/channels failing the same way (looks like blocking/403s)
- About to change `player_client` order as the first troubleshooting step
- About to raise the throttling knobs because failures "look like" rate limiting

**NOT for** `not released yet` / `still processing after live stream` deferrals — that's release-detection, not a failure. See the next section.

## Not a Download Failure: post-live / "still processing" deferrals

`Deferring … — not released yet: Video is still processing after live stream` (also "currently live" / "upcoming premiere") is **not** a yt-dlp failure — it's yt-hoarder's release-detection gate (`is_video_ready_for_download`, `backend/app/ytdlp/info.py`) deliberately deferring. Skip the download-failure checklist below.

**The trap:** `post_live` is NOT proof the VOD is unavailable. yt-dlp keeps a finished livestream flagged `live_status=post_live` for hours/days after a complete, downloadable VOD already exists — `availability` can read `public` and the video be fully watchable meanwhile. So "still `post_live` → just wait" is usually WRONG for a stream that ended more than a few minutes ago.

**Diagnose by FORMATS, not `live_status`** — checking `live_status`/`availability` alone is exactly the misdiagnosis this section prevents:
```
yt-dlp -F --skip-download "<url>"
```
A normal resolution ladder with DASH VOD formats (`http_dash_segments`, e.g. `137`/`136`/`135`…) — not just a bare live `m3u8` — means it IS downloadable despite the `post_live` label. **Zero** real audio/video formats → genuinely still processing; the deferral is correct, wait.

The gate treats `post_live` as ready once real formats exist (only genuine live/upcoming, or a just-ended stream with no formats yet, stays deferred). A build that blanket-defers *any* `post_live` regardless of formats is running stale **yt-hoarder app code** (this is app logic in `ytdlp/info.py`, not a yt-dlp version) — rebuild/restart the backend to pick up the current gate. A stuck NOT_READY item clears on the next subscription scan (~10 min) or via manual Retry; no DB surgery.

## Download-phase 403: transfer fails after extraction succeeds

`unable to download video data: HTTP Error 403: Forbidden` with a clean extraction is **not throttling-shaped**. Throttling and bot-detection surface during *extraction* — 429s, bot checks, `Sign in to confirm`. A successful extraction followed by a rejected media transfer means YouTube served the format URLs and then refused the bytes, which the throttle knobs do not address. Raising them here costs speed and fixes nothing — a 2026-08-19 session did exactly that and only made the deployment slower.

**Capture the error BEFORE cancelling or retrying.** A cancel overwrites `TaskRecord.status_message` with "Cancelled by user (bulk)", destroying the failure text even though `retry_count > 0` proves it really failed. Pull it first (`$COMPOSE` is whichever compose file is running — there is no default `docker-compose.yml`):

```bash
COMPOSE=docker-compose.dev.yml   # or docker-compose.published.yml / docker-compose.prod.yml
docker compose -f $COMPOSE exec -T postgres psql -U ytdl -d yt_hoarder -x -c \
  "SELECT task_id, status, retry_count, status_message, download_job_url
   FROM task_records WHERE status IN ('RETRY','FAILED') ORDER BY updated_at DESC LIMIT 5;"
docker compose -f $COMPOSE logs backend --tail 300 > /tmp/403.log
```

### Reproducing it — three traps that produce false results

1. **Use a FULL download.** yt-dlp's `test: True` (~10 KB) and single ranged GETs cannot reach this failure: these formats transfer in 10 MB chunks (`downloader_options: {'http_chunk_size': 10485760}` on the format dicts), and the failure lands on a later chunk. A truncated test that "passes" proves only that the *first* chunk succeeded — this invalidated most of a session's testing.
2. **Use the app's real format selector.** A bare `yt-dlp <url>` uses yt-dlp's default `bv*+ba/b` and picks different formats than the app's `best_ios` chain (`ytdlp/options.py:240-248`), which tries three adaptive H.264+AAC branches before falling back to `best[ext=mp4]` (itag 18). Testing without `-f` selects formats the app never requests, and has already produced a false root-cause once.
3. **Dump the app's real options — don't infer them.** Settings drift (someone raises a throttle knob while troubleshooting) and the difference is invisible in the logs:

```bash
docker compose -f $COMPOSE exec -T backend python -c "
import sys; sys.path.insert(0, '/app')
from database import db; db.initialize_database()
from types import SimpleNamespace
from ytdlp.options import create_ydl_options
job = SimpleNamespace(url='<URL>', overwrite=False, audio_only=False, generate_transcript=True)
o = create_ydl_options(job, quality='best_ios', sub_directory='', extract_flat=False, cookie_file=None)
o.pop('logger', None); o.pop('progress_hooks', None)
[print('%-22s %s' % (k, str(o[k])[:150])) for k in sorted(o)]"
```

`db.initialize_database()` is required — the engines are built in the lifespan, so a bare `python -c` gets `'NoneType' object is not callable` from `sync_session()` without it.

### The decisive test: bare container, concurrently

Run a full download in a throwaway container from the same image **while the app is failing** — same URL, same client list, same selector. It is cheap and it collapses the whole environmental cause space at once:

- **Container succeeds while the app fails** → not YouTube-side, not IP reputation, not the throttle, not the client list. The cause is specific to the app process.
- **Both fail** → environmental. Now the extraction-phase checklist and the throttle knobs are worth considering.

Drive `YoutubeDL` in-process (mirroring `tasks/downloads.py`: `extract_info(download=False)` then `process_ie_result(info, download=True)`) rather than shelling out, so the format selector and options match. Run with `python -u` — buffered output withholds everything until exit, and a full download takes minutes.

## Diagnostic Checklist (cheapest to most involved)

Checks below state *requirements*, not today's pinned values — pinned versions drift every time someone bumps a dependency and this file doesn't auto-update. Always read the current value from the named file, don't trust a memorized number.

1. **Deno runtime.** `docker exec <container> deno --version` — requirement: ≥2.3.0, auto-discovered from PATH. Pin lives in `Dockerfile.prod` (`DENO_VERSION` ARG, one place for both the prod and dev images) — check that file for the current pinned value. Missing/too-old Deno is the actual cause of `Signature solving failed` cascading into `Requested format is not available` — not player-client.
2. **yt-dlp-ejs present.** Declared transitively via `yt-dlp[default]` in `backend/pyproject.toml` — check `backend/uv.lock` for whether it's resolved/present. No specific version requirement documented; just needs to exist alongside Deno for challenge solving to work at all.
3. **yt-dlp itself current.** Requirement: ≥2025.11.12 for JS-runtime challenge solving to exist at all (per CLAUDE.md). Check `backend/pyproject.toml` for the actual pinned floor. Rebuild the image to update — README.md already gives this as the one-line fix for empty downloads.
4. **Blanket vs isolated.** Same error across many unrelated videos → points to Deno/yt-dlp (steps 1-3). Failure on one specific video/channel only → likely video-specific (region lock, DRM, age-gate without cookies), not systemic.
5. **Rate limiting — extraction-phase only.** Applies to 429s, bot checks and `Sign in to confirm` *during extraction*, or blocking across sequential channel walks. **Do not reach for these for a transfer-phase 403** (see the section above): a clean extraction followed by a rejected transfer is not throttling, and these knobs will only slow the deployment down. Three `app_settings` knobs, all editable in the Settings UI, defaults in `backend/app/models.py` `APP_SETTINGS_DEFAULTS`. Reach for them in this order:
   - `request_sleep_seconds` (default 0) → yt-dlp `sleep_interval_requests`. Try 1–2 first: it paces the request *pattern*, which is what bot detection keys on, and it is the only one of the three that also covers metadata extraction (set in both `ytdlp/options.py` and `ytdlp/info.py`). Costs channel-enumeration speed. Server-capped at 60.
   - `download_sleep_seconds` (default 60) → a Python sleep between jobs, and **only for subscription/playlist downloads** — `_rate_limit_sleep` (`backend/app/tasks/downloads.py`) returns early for one-offs, so raising it does nothing for a manually-submitted URL.
   - `download_rate_limit_kbps` (default 0 = unlimited) → yt-dlp `ratelimit`, converted to bytes/sec. Weakest lever of the three; per job body, so N parallel downloads use N × the cap. Ignored when a format falls through to the ffmpeg downloader.
6. **Player-client, last.** Defaults (`DEFAULT_PLAYER_CLIENTS` / `DEFAULT_COOKIES_PLAYER_CLIENTS`, `backend/app/models.py:124,129`) only seed *new* `app_settings` rows — an existing instance keeps its stored DB value; editing `models.py` never retroactively fixes a running install. Change the live value via Settings UI (`PUT /settings`, validated against `VALID_PLAYER_CLIENTS` in `backend/app/routers/settings.py:191-198`). Consumed in `backend/app/ytdlp/info.py:107-114` (metadata) and `backend/app/ytdlp/options.py:394-401` (download). Watch for clients yt-dlp removed upstream (e.g. `tv_embedded`) — a stored list still referencing one looks like "misconfiguration" but the fix is deleting the stale name. `player_client` is DB-only — it is not in `config.yml`/`config.sample.yml`.

   **`android_vr` (since 2026-08-17).** YouTube 403s this client's *progressive* format — yt-dlp [#17456](https://github.com/yt-dlp/yt-dlp/issues/17456), fixed by [#17461](https://github.com/yt-dlp/yt-dlp/pull/17461), which dropped it from yt-dlp's own defaults. Measured locally: `android_vr` + itag `18` → 403; `android_vr` + adaptive (`395+251`, `133+140`) → OK; `tv_simply` + itag `18` → OK. **Two limits, or this becomes the next false lead:** the app's `best_ios` selector reaches itag 18 only when a video offers no adaptive H.264+AAC pair, so most videos never touch the broken path; and a confirmed 2026-08-19 failure occurred with `android_vr` already removed, so its presence is not sufficient evidence of cause. Note a yt-dlp bump does *not* apply upstream's default change — the app passes `player_client` explicitly via `extractor_args`, overriding yt-dlp's `_DEFAULT_CLIENTS`.

   The app dropped it from its own defaults on the 2026.08.19 bump (`realign_player_clients` rewrites stored rows too), so on a current install `android_vr` in the live list means somebody put it back. What made it worse than the 403 alone: 2026.08.19 also gave `android_vr` a *required* GVS PO-token policy, so without a token its adaptive formats are filtered out and only itag 18 survives selection — the one format that 403s. Same trap now applies to any POT-required client if bgutil-pot is missing; confirm with `youtube:formats=missing_pot`, which shows what a token would restore.

7. **Retries do not vary what you probably think they vary.** The client list comes from one branch (`ytdlp/options.py:395-396`): `cookies_player_client if cookie_file else player_client`. The only per-attempt variation is `cookie_file`, from `cookie_session(is_retry=ctx.attempt > 0)` (`tasks/downloads.py:723`), which yields `None` unless `/data/cookies.txt` exists — so with no cookie file every attempt sends the same client list. Impersonation, by contrast, **is** re-randomized per attempt: `_build_download_options` runs inside the job body and `create_ydl_options` (`ytdlp/options.py:372-373`) calls `get_random_impersonate_target()` each time. So retries change the TLS fingerprint but never the clients. `_download_with_fallback` (`tasks/downloads.py:521-575`) changes only the format selector and fires only on `requested format is not available` — a 403 does not match it.

## Quick Reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `not released yet` / `still processing after live stream` (deferred, not a failure) | Lingering yt-dlp `post_live` flag on a finished stream — VOD actually downloadable | Not a download failure — `yt-dlp -F` shows real formats; the release gate downloads once formats exist |
| `Signature solving failed` | Deno missing/too old, or yt-dlp-ejs missing | Verify Deno ≥2.3.0 in container; rebuild image |
| `Requested format is not available` right after a sig failure | Downstream of signature-solving failure | Fix Deno/yt-dlp first, don't touch player_client |
| Empty/zero-byte file | Stale yt-dlp in image | Rebuild image |
| `unable to download video data: HTTP Error 403` — extraction clean, fails **during transfer** | NOT rate limiting | Reproduce with a **full** download + the app's `-f`; run a bare container concurrently. Change no settings first |
| 429 / bot-check / `Sign in to confirm` **during extraction** | Rate limiting | `request_sleep_seconds` 1–2 in Settings first, then `download_sleep_seconds` / `download_rate_limit_kbps` |
| One stored client is invalid/rejected | Stale/removed client name in DB, not `models.py` defaults | Fix via Settings UI / `PUT /settings` |
| One video/channel fails, others fine | Video-specific (region, DRM, age-gate) | Not a config issue |
| App fails but a bare container succeeds on the same URL, same minute | App-specific — not YouTube, IP, throttle or clients | Diff the app's real options (dump via `create_ydl_options`) against the container's |

## Common Mistakes
- Reordering `player_client` before checking Deno — the recurring mistake this skill exists to prevent.
- Assuming a `models.py` default change fixes a live instance (it doesn't; DB value is separate).
- Looking for `player_client` in `config.yml` — it's DB-only (`app_settings` table).
- Treating `Requested format is not available` as root cause when it's usually downstream of a signature failure.
- Treating a `post_live` / "still processing after live stream" **deferral** as a download failure OR as "correctly still processing, just wait" — a finished stream keeps the `post_live` flag for hours/days while a complete VOD exists. Check `yt-dlp -F` for real formats instead of trusting `live_status`/`availability`.
- Do not repeat "Only images available" as an observed error string — it does not appear in this codebase or docs; treat any user mention of it as unverified.
- **Treating a transfer-phase 403 as rate limiting** and raising the throttle knobs — the most expensive mistake available here, because it looks productive: it slows every metadata fetch and channel walk (`sleep_interval_requests` is paid per request in *both* option builders) while leaving the failure untouched.
- **Reproducing with a truncated download** — `test: True` or a single ranged GET stops after ~10 KB and structurally cannot reach a failure that lands on a later 10 MB chunk. "It passed" then means nothing.
- **Reproducing without the app's `-f` selector** — bare `yt-dlp <url>` uses `bv*+ba/b` and tests formats the app never requests.
- **Assuming retries rotate player clients.** They don't — only cookies (and only if `/data/cookies.txt` exists) and impersonation vary per attempt. See checklist item 7.
- **Expecting to identify the culprit client from the info dict.** yt-dlp tags raw streaming data with `STREAMING_DATA_CLIENT_NAME = '__yt_dlp_client'` (`yt_dlp/extractor/youtube/_video.py`), but the key does **not** survive into the final format dicts — it reads `None` on every format. Attribution requires probing clients one at a time, or reading yt-dlp's own log lines (which do name the client on skip warnings).
- **Cancelling or retrying a failed task before capturing `status_message`** — the cancel overwrites it and the original error is gone.
- **Concluding a cause from a single passing or failing run.** Four separate theories were confirmed-then-disproven in one 2026-08-19 session (`android_vr`, "condition already passed", impersonation fingerprint, throttling). Verify a fix by toggling the suspected cause back and forth, not by one success after one change.

## Key Files
- `Dockerfile.prod` — Deno version pin (`DENO_VERSION` ARG); builds both the prod and dev images
- `backend/uv.lock` — yt-dlp-ejs pin; `backend/pyproject.toml` — yt-dlp version floor
- `backend/app/models.py:101-129,725` — `VALID_PLAYER_CLIENTS`, `DEFAULT_PLAYER_CLIENTS`, `DEFAULT_COOKIES_PLAYER_CLIENTS`, `APP_SETTINGS_DEFAULTS`
- `backend/app/routers/settings.py:191-198` — `PUT /settings` player-client validation
- `backend/app/ytdlp/options.py:394-401` (download) and `backend/app/ytdlp/info.py:107-114` (metadata) — where player_client is applied
- `backend/app/ytdlp/options.py:240-248` — `get_format`'s `best_ios` chain; determines whether a download ever reaches itag 18
- `backend/app/ytdlp/options.py:372-373` — impersonation target selection (random when anonymous, stable when a cookie file is present)
- `backend/app/ytdlp/options.py` `_throttling_options` — the throttling knobs' single conversion point for downloads; `ytdlp/info.py` sets `sleep_interval_requests` separately for metadata
- `backend/app/tasks/downloads.py:723` (cookie/attempt branch), `:734` (the `attempt=/clients=` log line — the phase marker), `:800-808` (the catch-all that turns a 403 into a retry)
- `backend/app/ytdlp/info.py` — `is_video_ready_for_download` + `_has_downloadable_formats` release-detection gate (live/upcoming defer; post-live defers only when no downloadable formats yet)
- `.claude/rules/ytdlp.md` (`# yt-dlp configuration`), `AGENTS.md` (`## yt-dlp Configuration`), `README.md:186`, `docs/TROUBLESHOOTING.md:36-46` — existing condensed notes (this skill goes deeper)
