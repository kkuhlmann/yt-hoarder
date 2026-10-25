---
name: upgrading-yt-dlp
description: >-
  Use when bumping yt-dlp in YT-Hoarder — editing the yt-dlp floor in backend/pyproject.toml,
  running `uv lock --upgrade-package yt-dlp`, reviewing or merging a Dependabot PR that moves
  yt-dlp in backend/uv.lock, or when asked whether a newer yt-dlp is out. Reviews the release
  notes, the YouTube PRs' comment threads and yt-dlp's client table so the app's player-client
  defaults, valid-client list, data migration and Deno/curl-cffi pins change with it. Not for a
  download failing right now — that is yt-dlp-diagnostics.
---

# Upgrading yt-dlp

## Why every bump is a review

The app passes `player_client` explicitly in `extractor_args` (`_client_options` in
`ytdlp/options.py`, and `ytdlp/info.py`), which overrides yt-dlp's own `_DEFAULT_CLIENTS`. So:

- an upstream default change — a new client, a demoted one, a new order — **never reaches the app
  through the lock**;
- the lists that run are the stored `app_settings` row, not `models.py`, so a default change also
  needs a data migration.

A lock-only bump of a release that demoted a client keeps the app on the broken client indefinitely.
It has happened twice: `android_vr` (2026.08.19) and `tv_downgraded` (2026.10.21). Dependabot's
Monday `uv` run produces exactly that lock-only bump — do the review on its branch, or supersede it
(it closes itself once main has the version).

## 1. Find the range

```bash
grep -A1 '^name = "yt-dlp"$' backend/uv.lock
curl -s https://pypi.org/pypi/yt-dlp/json | python3 -I -c "
import json, sys
d = json.load(sys.stdin)
print(sorted((f[0]['upload_time'][:10], v) for v, f in d['releases'].items() if f and 'dev' not in v)[-6:])"
```

Review **every** stable release after the locked one, not only the newest: a demotion is announced
once, in the release that makes it.

- GitHub tags are zero-padded (`2026.08.19`); PyPI versions are not (`2026.8.19`).
  `releases/tags/2026.8.19` 404s.
- There is no `gh` on this host. Unauthenticated `curl` to api.github.com gets 60 requests an hour,
  and a bump with five YouTube PRs needs about twenty — pass `per_page=100` and save responses into
  the scratchpad instead of refetching.

## 2. Read the release notes

`curl -s https://api.github.com/repos/yt-dlp/yt-dlp/releases/tags/<YYYY.MM.DD>`, field `body`. Read:

- **Important changes** — security notices, deprecations, requirement changes;
- every **youtube** entry under Extractor changes — clients added, removed or made default, UA
  changes, PO-token policy changes;
- **Networking / Request Handler** — curl_cffi and impersonation;
- **Misc → build / requirements** — dependency floors (Deno, yt-dlp-ejs, curl-cffi, certifi);
- anything under **jsc**, **ejs** or **pot**.

## 3. Read each YouTube PR's threads

The notes say *what* changed; the threads say *why* and *for how long*. For each YouTube PR:

```bash
B=https://api.github.com/repos/yt-dlp/yt-dlp
curl -s $B/pulls/N                                   # body, including "Fixes #…"
curl -s "$B/issues/N/comments?per_page=100"          # conversation
curl -s "$B/pulls/N/comments?per_page=100"           # review comments
```

Then each issue it fixes (`$B/issues/N`). Weight comments by `author_association`: MEMBER and
COLLABORATOR are maintainers. Copy caveats down word for word ("a temporary solution until EJS is
fixed", "we need to correct visionos' POT flags … next"); they go into *Open upstream threads*.
Also look for breakage reported since the release, per client the release touched:
`curl -s "https://api.github.com/search/issues?q=repo:yt-dlp/yt-dlp+<client>+created:>=<release-date>"`.

## 4. Diff the client table

```bash
S=<scratchpad>/dumps; mkdir -p $S
for v in <old> <new>; do
  uv run --no-project --isolated --with "yt-dlp[default]==$v" python -I \
    .claude/skills/yt-dlp-upgrade/scripts/dump_youtube_clients.py \
    --app-models backend/app/models.py > $S/$v.json 2> $S/$v.paths
done
diff $S/<old>.json $S/<new>.json
```

The dump holds the four upstream default lists, every public client's PO-token policy per protocol,
its JS-player, cookie and auth requirements, the dependency floors, and two of the app's private
couplings. Check `yt_dlp_version` in each file: without `--no-project --isolated`, both runs import
the backend venv's yt-dlp and the diff is empty. **When you're done, the new version's `app_check`
must be all-empty** — `missing_from_app`, `gone_upstream`, `defaults_not_valid`,
`cookie_defaults_without_cookie_support`.

Then `diff -u` the source files the script prints to stderr (`_base.py`, `_video.py`, `pot/`).
**Demotions often live only in comments**: `android_vr`'s "ALL formats … are 403'd" sat above its
`INNERTUBE_CLIENTS` entry, with no field the dump can see.

## 5. Requirements and pins

| What | Upstream says | App pin | Action |
|---|---|---|---|
| Deno | `deno>=X; extra == 'deno'` | `ARG DENO_VERSION=` in `Dockerfile.prod` | If the floor is above the pin, bump it in the same change — keep `ARG DENO_VERSION=vX` on its own line, the form `pinned-release-check.yml` greps — or merge the bot's open `pin/deno-version-*` PR. Confirm both linux zips exist on the Deno release. |
| yt-dlp-ejs | `yt-dlp-ejs==X` in `default` | `backend/uv.lock` | Follows the lock. |
| curl-cffi | range in the `curl-cffi` extra; tested version in `pin-curl-cffi` | `backend/uv.lock` | `--upgrade-package curl-cffi` to the tested version when it is in range. It supplies the impersonation targets. |
| New floors (certifi, …) | `default` extra | `backend/uv.lock` | Follow the lock; name a security fix in the commit message. |
| bgutil-pot | its releases page | `ARG BGUTIL_POT_VERSION=` | Read its releases for yt-dlp compatibility notes; bumps arrive as `pinned-release-check.yml` PRs. |

## 6. Re-verify the app's couplings to yt-dlp internals

Each of these fails silently if upstream changes it. The dump checks the first two; grep the new
source for the rest.

- `YoutubeDL._get_available_impersonate_targets` (`ytdlp/options.py`) — renamed means impersonation
  silently turns off.
- `STREAMING_DATA_CLIENT_NAME` — the diagnostics skill relies on it.
- The POT provider key, derived from the provider's class name (`PROVIDER_KEY` in `pot/_provider.py`)
  — `_get_pot_extractor_args`'s `youtubepot-bgutilcli` key depends on it (`.claude/rules/ytdlp.md`).
- `'Unable to download video subtitles'` in `YoutubeDL.py` → `SUBTITLE_DOWNLOAD_ERROR`
  (`ytdlp/subtitles.py`). Reworded upstream, caption failures fail downloads again.
- The `tlang` translation tracks under `automatic_captions`, and subtitle selection taking the last
  match → `prefer_untranslated_captions`.

## 7. Decide the lists

Follow upstream, and keep the app's deliberate extras:

- **Anonymous** (`DEFAULT_PLAYER_CLIENTS`): upstream's `_DEFAULT_CLIENTS` in upstream's order, then
  the app's extra depth (a PO-token client, an HLS client, an embed fallback) unless upstream
  removed or demoted one of them.
- **Cookies** (`DEFAULT_COOKIES_PLAYER_CLIENTS`): `supports_cookies` clients only — upstream's
  `_DEFAULT_AUTHED_CLIENTS` in order, then the app's extra cookie-capable ones.
- **Added client** → `VALID_PLAYER_CLIENTS` in its PO-token group with a capability comment,
  `settings/config.ts`, and `CookieFileSection.tsx` only if it supports cookies.
- **Removed client** → out of everything, and the migration strips it from stored rows: yt-dlp warns
  `Skipping unsupported client`, and `PUT /settings` rejects the whole list until it is gone.
- **Demoted client** (still exists, broken) → stays selectable but moves to the "do not default to
  it" group at the end of `VALID_PLAYER_CLIENTS` with its reason and issue number; it leaves the
  defaults, sinks to the end of both frontend lists with a description that says why, and
  `docs/TROUBLESHOOTING.md` gets its error text.

## 8. Apply

1. `backend/pyproject.toml` floor, then `cd backend && uv lock --upgrade-package yt-dlp` (plus
   `--upgrade-package curl-cffi` per step 5). Check the lock diff moves only what you expect.
2. `Dockerfile.prod` pins, per step 5.
3. `backend/app/models.py` — `VALID_PLAYER_CLIENTS` and its comments, `DEFAULT_PLAYER_CLIENTS`,
   `DEFAULT_COOKIES_PLAYER_CLIENTS`, including the comments explaining each order.
4. `backend/migrations/versions/baseline_schema.py` — the seeded `app_settings` literals must equal
   the new constants.
5. A data migration, whenever a default changed: copy `adopt_tv_samsung_client.py`, with
   `down_revision` from `cd backend && uv run alembic heads`. It **rewrites the stored lists
   unconditionally** (the user's call, 2026-10-25), and its downgrade restores the previous defaults.
   `test_migration_defaults.py` runs `upgrade head` on a fresh database, so the chain must end on the
   constants.
6. `frontend/app/_components/settings/config.ts` and `CookieFileSection.tsx` option lists.
7. `.claude/rules/ytdlp.md` (its default-list line), `docs/TROUBLESHOOTING.md`, and the
   `yt-dlp-diagnostics` skill for any new failure signature.
8. *Open upstream threads* below.

If the review finds nothing to change, still bump the floor and the lock, and say in the commit
message which releases were reviewed and why nothing else moved.

## 9. Verify

- Backend, with Docker up: `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest`.
- Migration round trip on a scratch database (`upgrade head` → `downgrade -1` → `upgrade head`),
  aimed the way `tests/test_migration_defaults.py` does it: `DATABASE__URL` plus a working directory
  with no `config.yml` above it.
- Frontend, mirroring CI: `docker compose -f docker-compose.dev.yml exec -T frontend sh -c 'npx tsc --noEmit -p tsconfig.json && npm run lint && npm test && npm run build'`
  when the dev stack runs; otherwise `run --rm --no-deps -T frontend sh -c 'npm ci --no-audit --no-fund && …'`.
- **Live probe, without rebuilding or touching the running stack.** Unzip the new yt-dlp wheel —
  and a new Deno binary, if bumped — into fresh scratchpad directories, then:

  ```bash
  docker run --rm -v <wheel-dir>:/ytdlp:ro -v <deno-dir>/deno:/usr/local/bin/deno:ro \
    -e PYTHONPATH=/ytdlp --entrypoint /opt/venv/bin/python yt-hoarder:production \
    -m yt_dlp -v -F --extractor-args "youtube:player_client=<new anonymous list>" <public video url>
  ```

  Repeat with the cookie list (no cookies needed to list formats). Expect the right `deno` version
  under `[debug] JS runtimes`, a full adaptive ladder, no `Skipping unsupported client`, no
  signature or n-challenge failures, and no client-specific `UNPLAYABLE`. The image ships bgutil-pot,
  so PO-token clients get tokens.

## Open upstream threads

Re-check each on the next bump; delete what is resolved and add what that bump's threads raise.
Current as of 2026.10.21 (reviewed 2026-10-25).

- **`tv_samsung` is a workaround.** YouTube forces the TCL player-JS variant on TV clients, which
  breaks `tv_downgraded` ("The page needs to be reloaded", #17389); `tv_samsung`'s device details
  dodge it, and a maintainer called it "a temporary solution until EJS is fixed" (#17723). If
  yt-dlp-ejs learns the variant, `tv_downgraded` may recover and `tv_samsung` may be dropped. It
  leads the anonymous list and is second in the cookie list.
- **`visionos`' PO-token flags are due to change.** bashonly, reviewing #17805: "we need to correct
  `visionos`' POT flags and add `visionos_downgraded` next". `visionos` is second in the anonymous
  list on the strength of needing no token.
- **#17666 (open): `mweb` and `web_embedded` are SABR-only in some sessions** (format 18 aside).
  `web_embedded`'s Safari UA (#17684) brings back HLS 91–96 there. Both are in the cookie list.
- **`web_safari` HLS needs a "trusted" session since 2026.07**, per the comment over its
  `INNERTUBE_CLIENTS` entry. It holds the anonymous list's HLS slot; if that hardens, it no longer
  earns it.

## Common mistakes

- **Bumping the lock alone** — what Dependabot does. The app keeps its old explicit lists.
- **Editing `models.py` defaults without a migration** — only fresh installs get them.
- **Reading only the newest release** when several have shipped since the last bump.
- **Reading only the release notes** — the caveats are in the PR threads.
- **Trusting the dump alone** — demotions live in source comments.
- **Concluding from one probe.** Several breakages are experiment-gated per session (#17389,
  #17666): a clean `-F` shows the client works for *this* session, not that the thread is wrong.
