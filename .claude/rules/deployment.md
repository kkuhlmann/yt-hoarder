---
paths:
  - "**/Dockerfile*"
  - "docker-compose*.yml"
  - ".github/workflows/**/*"
  - "Taskfile*.yml"
  - "setup.sh"
  - "config.sample.yml"
  - ".env.sample"
  - "backend/app/config.py"
---

# Images, compose, Taskfile and configuration plumbing

Root `AGENTS.md` carries the compose-file structure and the `config.yml` > env var > default rule.
This is everything that only matters when you are editing the build, release or config machinery.
See also `docs/RELEASING.md` for the human release procedure.

## Adding a Taskfile target that touches a container — three traps

- **Use `-f {{.COMPOSE_FILE}}`.** There is no default `docker-compose.yml`, so a bare `docker compose exec` fails with `no configuration file provided` — this silently broke six targets. `COMPOSE_FILE` is a global var holding a `$(…)` shell substitution that picks dev or prod based on which stack is running; because it's a plain string (not a `sh:` var) it costs nothing on `task --list` and is evaluated only by the tasks that interpolate it. Don't use the `dev 2>/dev/null || prod` fallback that `logs`/`logs-backend` use for anything stateful or interactive — on failure it runs the command twice.
- **Never redirect a container's stdio to a host file** (`> file` / `< file`). Under Task's built-in shell interpreter that makes `docker compose` fail with `write /dev/stdout: bad file descriptor`, and an `sh -c` wrapper does *not* help. `db:backup` hit this and the failure mode is nasty: pg_dump exits 0 and you get a **0-byte backup**. Stage through a file inside the container and move it with `docker compose cp`, as `db:backup`/`db:restore` now do.
- **`docker compose ps` / `images` ignore which `-f` you pass** — both resolve by project label, so `-f docker-compose.prod.yml ps` happily lists dev's `frontend`. Consequence: `COMPOSE_FILE`'s dev/prod probe has always taken its first branch, which is harmless (every consumer is exec-style and also resolves by project+service, so the named file only has to *define* `backend` and `postgres`) — don't "fix" it into a 3-way, and don't build mode detection on `ps --services`. Where mode genuinely matters, discriminate on the running **image**, as `_db:reset-impl` does: without that, `task clean` restarts a published install in dev and hands the user a surprise 45-minute build. Capture it in a task-level `sh:` var, which Task evaluates before the first cmd — after `down` there is nothing left to inspect. Match with `grep`, not `--format '{{.Image}}'`, which Task expands as its own template.

## Multi-architecture images

Images build natively for **both amd64 and arm64**; nothing is pinned to an architecture, and
`.github/workflows/release.yml` pushes a single multi-arch manifest to GHCR on `v*` tags, then opens
a GitHub Release page for the tag ([docs/RELEASING.md](../../docs/RELEASING.md)).
`docker-compose.published.yml` is the consumer of that manifest and the **default install path** —
`setup.sh` pulls `ghcr.io/kkuhlmann/yt-hoarder:latest` unless told otherwise. Nine things that are
easy to undo by accident:

- **`docker-compose.published.yml` must never gain a `build:` block.** With one, a failed pull stops
  being an error and silently becomes a 45-minute build — of the working tree, not the released
  commit. Its absence is also what lets `setup.sh` treat a non-zero `pull` as a real failure and offer
  the from-source path explicitly.
- **The published stack has to be installable from files alone, because `setup.sh` fetches them.**
  Run outside a checkout — the documented `wget setup.sh` install — it downloads
  `docker-compose.published.yml`, `docker-compose.common.yml` and `config.sample.yml` from
  `raw.githubusercontent` and generates the rest. Common is in that list because every service in the
  published file is an `extends: file:` of it, so the published file alone starts nothing. The
  consequence for edits: a **new host-path mount on `backend-common`** is a new file the installer
  must fetch or generate, and it breaks `wget` installs only — a checkout keeps working, so nothing
  local tells you. The two flags that carry this are `HAVE_SOURCE` (`Dockerfile.prod` + `backend/`
  present → build modes offered, and never fetch, since those files are tracked source someone may be
  editing) and `HAVE_COMPOSE` (→ configure the directory in place rather than nesting another one).
- **`latest` must only ever point at a released tag.** `release.yml` deliberately lists no `latest`
  rule: metadata-action's default `latest=auto` already emits it for a semver tag push and skips
  prereleases. Re-adding `type=raw,value=latest,enable={{is_default_branch}}` would let a
  `workflow_dispatch` on main publish an unreleased build straight into every new user's install.
  One thing `latest=auto` does *not* do is compare versions, so a hotfix released on an older line
  after a newer minor has shipped moves `latest` backwards onto the older code.
- **Every version is tagged twice, `v0.1.0` and `0.1.0`, and the `v` form is the load-bearing one.**
  metadata-action strips the leading `v` from `{{version}}`, but `setup.sh` (`--image-tag`, and the
  `^v[0-9]` branch that reuses the tag as a *git* ref to fetch compose files from),
  `docker-compose.published.yml` and every doc name the `v` form — so emitting only the bare one
  breaks the documented pin with `manifest unknown`, and only for users, never in CI.
- **`github_release` creates the Release page only when one doesn't already exist, and never edits.**
  Both release routes converge on the same `push: tags` event — a web-UI publish creates the tag —
  so on that route the job runs *after* someone has written the notes by hand. An unconditional
  `gh release create` fails there, and `--notes`/`edit` would overwrite them. It also `needs:
  publish`, so the page can never announce a version whose image failed to build. `contents: write`
  is scoped to that one job; the top-level token stays read-only.
- **Never reintroduce `platform:` / `platforms:` into the compose files.** Those keys pinned everything
  to amd64 and forced the backend under QEMU on ARM hosts. Omitting them makes Docker use the host
  platform, and `DOCKER_DEFAULT_PLATFORM` is the per-user override if someone genuinely needs one.
  Listing both arches under a compose `build` would also just fail — the default `docker` driver
  cannot produce a manifest list.
- **`ffmpeg_download.py` requires `TARGETPLATFORM` and exits non-zero without it.** Defaulting it to
  `linux/amd64` would bake x86-64 ffmpeg into arm64 images, which surfaces only as `exec format
  error` at transcode time, long after a green build. Guessing the *build* host's arch would be wrong
  too, since this cross-builds.
- **`frontend-builder` is `FROM --platform=$BUILDPLATFORM`**, not TARGETPLATFORM. Its only output is a
  Next static export — no native binaries — so it is arch-neutral and safe in either image, and
  pinning it to the builder keeps `npm ci` + `next build` out of emulation, where they dominated the
  cross-build. It also makes that layer byte-identical across arches, so the registry stores it once.
- **Do not "clean up untagged versions" on the GHCR package.** Now that this package is what strangers
  install, breaking it breaks every new deployment, not just a convenience path. In a manifest list the
  per-arch children
  (and the provenance attestations) *are* untagged versions — only the index carries the tag — so
  `actions/delete-package-versions` with `delete-only-untagged-versions` deletes the amd64/arm64 images
  out from under a working tag and pulls fail with manifest-unknown. Use a manifest-list-aware cleaner.

On the ML stack: ctranslate2's aarch64 wheel carries **Ruy** (ARM NEON int8) instead of the x86 build's
MKL/oneDNN, which is why `transcript.py`'s hardcoded `compute_type='int8'` is the portable choice —
`float16` on CPU would not be. It has NEON+dotprod but no i8mm, so arm64 transcription is correct but
slower than comparable x86.

## Configuration plumbing

**Under Docker, env vars mostly can't reach the app at all.** Neither compose file declares
`env_file:`, so `.env` is consumed *only* by Compose's own `${...}` interpolation; the sole var
passed into the backend is `FORWARDED_ALLOW_IPS`, via an explicit `environment:` entry in both
files. Adding a new env-tunable setting therefore means adding it to `environment:` too — otherwise
it silently does nothing in every containerized deployment. `.env` holds exactly what cannot live in
`config.yml` because it's consumed outside the backend process: the two host media paths (Compose
resolves the bind-mount source before the container exists), `YT_HOARDER_IMAGE`/`YT_HOARDER_TAG`
(they name the image, so they must resolve before there is a container to configure),
`NEXT_PUBLIC_BACKEND_API` and `ALLOWED_DEV_ORIGINS` (the Next.js dev server), and
`FORWARDED_ALLOW_IPS` (uvicorn itself, no `config.py` key). Everything else belongs in `config.yml`.

`storage.audio_path`/`storage.video_path` are pinned to the compose mount *targets* (`/mnt/audio`,
`/mnt/video`) — they are the container side, not the host side, and editing them makes the app write
to an unmounted path. `setup.sh` deliberately omits them, and `embedding.model`, from the config.yml
it generates: both already equal the code defaults and both are traps if changed.

`config.py` loads it with `pydantic-settings` + custom YAML loading, cached via `@lru_cache` at
startup, nested Pydantic models for validation. Import with `from config import settings`.

Three `.env` values that bite:
- `NEXT_PUBLIC_BACKEND_API` — dev-mode only, and **normally empty**: `app/lib/api.ts` then derives the API origin from `window.location` at runtime, which is what lets one dev server answer on localhost, a LAN IP and a Tailscale name at once. It is a `docker-compose.dev.yml` **`environment:` entry, not a build arg** — deliberately, since an ARG→ENV bake is what used to force a `--build` (and a setup-time prompt) whenever the address changed. Set it only when the API isn't on :8000 of the browsed host (reverse proxy, TLS). Prod and published hardcode `/api` in `Dockerfile.prod` and are unaffected; `api.ts` also guards on `NODE_ENV === 'production'` so a prod build that somehow lost that ENV falls back to same-origin rather than to `:8000`.
- `ALLOWED_DEV_ORIGINS` — comma-separated escape hatch for Next 16's `allowedDevOrigins` enforcement, now needed only for hosts no wildcard can match (single-label names, IPv6 literals).
- `YT_HOARDER_TAG` — read **only** by `docker-compose.published.yml`, so setting it under prod or dev does nothing at all. `setup.sh --image-tag` writes it here rather than using it once, so a later `docker compose pull` stays on the release the user chose.
