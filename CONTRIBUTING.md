# Contributing to yt-hoarder

This guide covers setting up a local development environment and the checks a pull request
has to pass. For end-user installation, see [`README.md`](README.md). For architecture, task
orchestration, and the reasoning behind non-obvious choices, see [`AGENTS.md`](AGENTS.md) and the
area-specific deep dives it indexes in [`.claude/rules/`](.claude/rules).

## Prerequisites

**Running the application requires only Docker.** Python 3.14, Node 24, PostgreSQL, yt-dlp, Deno,
and Whisper all run inside containers via Docker Compose. Install
[Docker](https://docs.docker.com/get-docker/) with Compose v2 and start the app with `task dev` (or
`docker compose -f docker-compose.dev.yml up -d`).

Contributor workflows use one of the build-from-source modes. `docker-compose.published.yml` runs the
last released image from GHCR, so it will not reflect your working tree — it is the end-user install
path, not a development one.

**Host-side tooling is optional and only for development** — running tests and the linter outside
Docker, and getting IDE autocomplete and type-checking, which needs the dependencies installed
locally.

## Bootstrap your dev environment

Run the bootstrap script to install the optional host tooling:

```bash
bash script/bootstrap.sh
```

With the Task runner already installed, the same thing is available as:

```bash
task setup:dev
```

The script is opt-in per tool, needs no `sudo`, and is safe to re-run — already-installed tools are
detected and skipped. It sets up:

| Tool | Why |
|------|-----|
| **uv** + `uv sync` (backend) | Python env, `pytest`, `ruff`, IDE type-checking |
| **node deps** (`npm ci`) | Frontend lint/build and IDE support (Node 24 itself must already be installed) |
| **task** | The `task <name>` shortcuts in `Taskfile.yml` |
| **deno** | yt-dlp challenge solving — only needed when running the backend *outside* Docker |

If a tool installs to a directory not on your `PATH` (e.g. `~/.local/bin`, `~/.deno/bin`), the script
prints the `export PATH=...` line to add to your shell rc.

## Development workflow

Most day-to-day work runs against the containers:

```bash
task dev            # start dev mode (frontend :3000, API :8000, hot reload)
task logs-backend   # follow backend logs
task down           # stop everything
```

Run `task help` for the full list. See [`TASKFILE_GUIDE.md`](TASKFILE_GUIDE.md) for common workflows
such as resetting the database and clearing pending tasks.

## Running tests & lint

**Backend checks run on the host** after bootstrapping. They are not available inside the backend
container, which is built with `uv sync --no-dev` and deliberately omits `pytest`, `ruff`, and the
other dev-only dependencies.

> **The backend tests need Docker running.** They spin up a throwaway PostgreSQL via
> [testcontainers](https://testcontainers.com/), so start the Docker daemon first. If it is stopped
> you get a confusing container-startup error rather than a normal test failure.

**Backend** (from `backend/`):

```bash
uv run pytest            # or: task backend:test
uv run ruff check .      # or: task backend:lint
uv run ruff format .     # or: task backend:format

uv run pytest tests/test_config.py    # or: task backend:test-file -- tests/test_config.py
```

The one ffmpeg-dependent test (the `/media/{id}/peaks` end-to-end case) skips itself on hosts without
`ffmpeg` on PATH, so a plain `uv run pytest` is clean — no `--deselect` needed. CI installs ffmpeg, so
it still runs there.

**Frontend** (from `frontend/`):

```bash
npm run lint             # or: task frontend:lint
npm test                 # or: task frontend:test
npm run build            # or: task frontend:build
```

Unlike the backend ones, the `task frontend:*` targets fall back to running inside the Docker
`frontend` container when the host has no Node — start it with `task dev` first.

Frontend tests use [Vitest](https://vitest.dev/) and are co-located next to their source as
`*.test.ts` / `*.test.tsx`. The default environment is `node`; a test that touches the DOM opts in
per-file with a `// @vitest-environment jsdom` docblock on its first line. See
[`.claude/rules/frontend.md`](.claude/rules/frontend.md) for the rest of the conventions.

## Code style

- **Backend:** Ruff formatter — single quotes, 100-char lines. Python 3.14, managed with `uv`.
- **Frontend:** TypeScript strict, and ESLint via flat config only (`eslint.config.mjs`). `next lint`
  was removed in Next 16 — do not reintroduce it or an `.eslintrc.json`.

Lint must stay at **0 errors**. The React Compiler warnings are a known baseline held at `warn` in
`eslint.config.mjs` and are worked down rule by rule, so do not add `--max-warnings` to the lint
command.

## Pull requests

CI runs the following on every pull request ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).
Run them locally first.

**Frontend** (from `frontend/`):

```bash
npx tsc --noEmit -p tsconfig.json   # noUnusedLocals is on: an unused import fails the build
npm run lint
npm test
npm run build
```

**Backend** (from `backend/`):

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

When a change touches user-facing behavior, config options, compose setup, or troubleshooting, update
[`README.md`](README.md) and the relevant file under [`docs/`](docs/) in the same pull request.

## License

By contributing, you agree that your contributions are licensed under the project's
[AGPL-3.0](LICENSE) license.
