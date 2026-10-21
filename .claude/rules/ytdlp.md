---
paths:
  - "backend/app/ytdlp/**/*"
---

# yt-dlp configuration

Root `AGENTS.md` carries the Deno requirement and the "defaults live in `models.py`" rule. For a
download that is failing right now — empty files, 403s, `Signature solving failed`, a video stuck as
not-yet-released — use the `yt-dlp-diagnostics` skill, which goes deeper than this file.

Browser impersonation and player-client settings avoid YouTube blocking:
- Random impersonation via `curl-cffi` (`ytdlp/options.py`); player-client fallback order set in the Settings UI (default `['visionos', 'tv_simply', 'web_safari', 'web', 'web_embedded']`)
- **Challenge solving**: yt-dlp ≥2025.11.12 needs an external JS runtime for YouTube `sig`/`n` challenges — **Deno ≥2.3.0** (auto-discovered from PATH) plus the `yt-dlp-ejs` dep. Deno is pinned via the `DENO_VERSION` ARG in `Dockerfile.prod`; too-old Deno → `Signature solving failed` → `Requested format is not available`
- Defaults live in `models.py` (`DEFAULT_PLAYER_CLIENTS` / `DEFAULT_COOKIES_PLAYER_CLIENTS`) and **only affect a fresh `app_settings` row** — existing installs keep stored values, so change them via the Settings UI, or ship a data migration as `realign_player_clients` does. Watch for clients yt-dlp removes upstream (e.g. `tv_embedded`) *and* for ones it keeps but demotes: `android_vr` still exists, but YouTube 403s every format it returns as of 2026-08-17, so a stored list still leading with it silently caps downloads at whatever the next client offers
- Downloads failing with empty files usually means yt-dlp needs updating or the player clients need adjusting
- **A caption 429 is usually a translated track, not rate limiting — the throttle knobs won't fix it.**
  YouTube auto-dubbing gives every dubbed audio track its own auto-caption track, and yt-dlp files a
  `tlang=en` machine translation of each under `en`, then takes the *last* (`process_subtitles`,
  `matches[-1]`). So a dubbed English video fetches, say, the Ukrainian dub's captions translated back,
  and YouTube 429s translation requests while serving the original track from the same IP seconds
  later (measured both orders, 2026-10-20). `skip=translated_subs` doesn't help: it only covers
  *manual* subtitles. Every site that fetches captions must call `prefer_untranslated_captions`
  (`ytdlp/subtitles.py`) **between `extract_info(download=False)` and `process_ie_result`**, which
  re-selects from `info['automatic_captions']`. A `pre_process` postprocessor runs too late, and a bare
  `ydl.download([url])` skips the hook entirely — which is why `_download_with_fallback`'s re-runs
  extract and process explicitly.
- **Throttling is three separate knobs, all `app_settings` columns (never `config.yml`), all 0 = off.**
  `download_sleep_seconds` sleeps *between* jobs and only for subscription/playlist ones
  (`_rate_limit_sleep` returns early otherwise). `download_rate_limit_kbps` → yt-dlp `ratelimit`,
  applied per job body, so it multiplies with `downloads_lane_concurrency` exactly like the sleep does.
  `request_sleep_seconds` → yt-dlp `sleep_interval_requests`, and it is deliberately set in **both**
  option builders — it is paid in `_request_webpage` during *extraction*, so downloads-only would miss
  the channel walks and populate fetches that are most of the request volume. `ratelimit` conversely
  stays out of `ytdlp/info.py`: metadata extraction has no media transfer to cap. `_throttling_options`
  (`ytdlp/options.py`) exists because inlining the two branches pushed `create_ydl_options` past ruff's
  C901 limit.
- **`_get_pot_extractor_args` deliberately emits two keys.** yt-dlp derives a POT provider's
  extractor-arg key from its *class* name (`pot/_provider.py` `PROVIDER_KEY` →
  `youtubepot-{key.lower()}`), so the CLI provider reads `youtubepot-bgutilcli:cli_path`. The
  second key, `youtubepot-bgutilscript:script_path`, is not a leftover: bgutil's HTTP provider reads
  it directly to decide whether a refused connection to its `127.0.0.1:4416` server is expected.
  Drop it and every extraction gains a warning. Unrecognised `youtubepot-*` keys are silently
  ignored, so neither mistake announces itself.
- **The cookie file is never handed to yt-dlp directly.** `YoutubeDL.close()` truncates and rewrites
  `cookiefile`, with no locking, and lanes run concurrently in one process — so a job that started
  earlier can overwrite a rotated session cookie a later job already persisted, and a run killed
  mid-close leaves a half-written file. `ytdlp/cookies.py`'s `cookie_session` is the single place that
  decides whether cookies apply *and* hands out a disposable copy. It is also the only place that
  knows metadata extraction has no attempt counter, so `RETRIES_ONLY` means never for it.
- **Impersonation is randomized only for anonymous requests.** `_get_impersonate_headers` keeps the
  extractor's per-client User-Agent, so an impersonated request pairs a browser TLS fingerprint with
  a smart-TV or Android-VR UA. Spread across anonymous requests that is cover; under one authenticated
  account a fingerprint that moves every job is a repeating mismatch, so `create_ydl_options` pins
  `get_stable_impersonate_target()` whenever `cookie_file` is set.
- **`DENO_VERSION` and `BGUTIL_POT_VERSION` are watched by a workflow, not by Dependabot.** Both are
  fetched by `curl` inside a `RUN`, and Dependabot's `docker` manager only parses `FROM` /
  `COPY --from` — so neither appears in `.github/dependabot.yml` and neither can. `Dockerfile.prod`
  must keep both as `ARG NAME=value` on their own line: `.github/workflows/pinned-release-check.yml`
  greps for exactly that, and its bump `sed` is anchored on the `=` so the bare re-declarations inside
  the builder stage keep inheriting. Do not "simplify" either pin back to `releases/latest` — that
  layer sits after the `uv.lock` COPY, so `latest` makes the PO-token provider update as a side
  effect of any Python dependency bump.
