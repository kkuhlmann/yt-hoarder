---
paths:
  - "backend/tests/**/*"
  - "backend/**/conftest.py"
---

# Backend test suite

How to run the suites and the "don't add `@pytest.mark.anyio`" rule live in the root `AGENTS.md`.
This is the fixture chain and why each piece of it is shaped the way it is.

Backend tests run on the **host** (`cd backend && uv run pytest`, or `task backend:test`) against a
session-scoped `postgres:16-alpine` testcontainer, so Docker must be up. pytest-asyncio is in `auto`
mode: a plain `async def` test just works — **don't add `@pytest.mark.anyio`**, it is redundant here
and only re-parametrizes the test id.

The fixture chain in `tests/conftest.py` is built for speed, and each piece of it is load-bearing:

- **The schema is created once per session** (`_db_engines`), not per test. Each test instead gets a
  clean slate at *setup* from `clean_database` (empty) or `test_database` (empty + seeded). Cleaning
  at setup rather than teardown is what makes a test that dies mid-transaction unable to poison the
  next one. Don't reintroduce per-test `create_all`/`drop_all` — that was ~0.83s per test.
- **Cleanup is `DELETE FROM` every table plus `ALTER SEQUENCE ... RESTART` on every sequence**, not
  `TRUNCATE`. On tables this small TRUNCATE's relfilenode churn costs ~6x more, and `RESTART
  IDENTITY` would miss standalone `task_queue_sequence` anyway. **Rewinding the sequences is not
  optional**: tests hit `/subscriptions/1` and `/tasks/1/retry`, `test_task_stats_shape_and_counts`
  asserts an exact dict over the seed, and `_populate_test_data` hardcodes
  `download_task_record_id=1`. Any replacement must still reproduce fresh-database id assignment.
- **`db._async_engine` / `db._sync_engine` and the two session factories are patched once per
  session and never restored.** That is only sound because the lifespan never runs: all ~82 client
  sites use a bare `TestClient(app)`, never `with TestClient(...)`. A test that needs the lifespan
  must save and restore those globals itself.
- **Never dispose the async engine in a sync teardown, and never drop its `NullPool`.** asyncpg
  connections are event-loop-bound; NullPool means none outlives a checkout, which is the only
  reason one engine can serve both pytest-asyncio's per-test loop and `TestClient`'s portal thread.
- **The container runs with `fsync=off -c full_page_writes=off -c synchronous_commit=off`** — it is
  thrown away at the end of the run, and durability was most of the per-test cleanup cost.
- **`_fast_bcrypt` patches `bcrypt.gensalt` to cost 4 suite-wide.** `checkpw` reads the cost from the
  hash, so register-then-login tests still exercise the real hash/verify path. Patching
  `auth.hash_password` instead would bypass the code `TestPasswordHashing` covers.
- **Keep the `_reset_rate_limiters` autouse fixture.** The limiters are module-level singletons and
  `authenticated_client` registers via a real `POST`, so without it the budget is consumed across
  unrelated tests.
- **Any test that reaches `transcript_embeddings` must request `pgvector_schema`**: transcript
  search, and `/stats/transcript-storage` too. `transcript_embeddings` and
  `transcript_blocks.text_search` live only in `baseline_schema.py`'s hand-written block, so
  `SQLModel.metadata.create_all` never creates them and those queries can't run without it. It is
  opt-in rather than autouse because most tests don't need it, and it needs no teardown —
  `transcript_embeddings` cascades off `transcript_blocks`, which `_reset_sql` already empties.
- **No private per-file engine fixtures — use `clean_database`.** A file that builds its own
  in-memory SQLite engine has to overwrite the `db` globals, and with session-scoped patching a
  failure to restore them poisons every later test. SQLite also silently skips FK enforcement, which
  is enough to make a sprite test pass vacuously.

Tests needing real ffmpeg/ffprobe carry `@requires_ffmpeg` (`tests/test_peaks.py`) and auto-skip on
hosts without it; CI installs ffmpeg and runs them.
