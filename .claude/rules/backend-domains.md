---
paths:
  - "backend/app/repositories/**/*"
  - "backend/app/routers/**/*"
  - "backend/app/services/**/*"
  - "backend/app/models.py"
  - "backend/migrations/**/*"
---

# Repositories, routers, services, models and migrations

Deep detail for the data and API layers. The `models.py` constraint list, the router-per-domain map
and the access-control tiers live in the root `AGENTS.md`.

## Repositories (`repositories/`) — two non-obvious bits

- `task_records/` is a package: `crud.py` (async), `retry.py` (downstream marking + retry/dispatch), `sync_ops.py` (sync, for job bodies), `bulk.py`. `__init__.py` re-exports the externally-used names so `from repositories import task_records` keeps working
- `base_access.py` is a factory — `create_access_functions()` generates share/unshare/has_access for the *three* structurally identical access repos (subscription, playlist, clip). **`media_access.py` is hand-written and cannot join them**: `MediaAccess` is unique on `(user_id, media_details_id, source_type, source_id)`, not `(user_id, fk)`, so one user legitimately holds several rows for the same media and revoking one source must leave the others standing. Every factory signature is 2-arg and its bulk insert hardcodes `index_elements=['user_id', fk]` — wrong constraint, wrong arity. The provenance columns are also what the extra surface exists for: source-scoped revokes, `has_direct_access`, and `get_transfer_candidate_user_ids` (ownership transfer on owner-delete, ordered DIRECT > SUBSCRIPTION > PLAYLIST). Note the owner check reads `MediaDetails.owner_id`, not the `user_id` the factory assumes

## `/semantic/search` scoping

**`/semantic/search` is scoped by the same `_build_media_conditions` the media list uses**, so the
group-folder drill-down, tag chips, minimum rating and the keyword box all narrow which transcripts
are searched. `transcript_embeddings` has no ORM model, so the bridge is
`build_media_scope_subquery`, which compiles those conditions to a SQL fragment spliced into the raw
`text()` queries. Four things there are load-bearing:
- **The compiled fragment's bind names must stay disjoint from the ones the search builders bind by
  hand** (`query`, `embedding`, `limit`, `fts_query`, …); `test_transcript_scope_sql.py` pins it.
  `literal_binds` would remove the problem and add an injection hole — the search string is user
  input.
- **It passes `include_owned=True`.** A soft delete calls `remove_all_access_for_media`, which drops
  every `MediaAccess` row *including the owner's*, so the access subquery alone hides an owner's own
  kept transcripts from them. This is the same gap the list closes with its `owner_id` fallback for
  DELETED/SKIPPED.
- **`status` is deliberately not threaded.** Transcript search spans every status, and passing one
  would also swap that widened access branch back to the narrower `owner_id`-only one.
- **A narrow scope runs through a `MATERIALIZED` CTE specifically to deny the planner the HNSW
  index.** pgvector post-filters an index scan, so a scoped `ORDER BY embedding <=> :q LIMIT n`
  returns far fewer rows than asked — an empty result, not an error — which is exactly what drilling
  into a group folder produces. Flattening that CTE back into the outer query reintroduces it
  silently. Above `_EXACT_SCOPE_MEDIA_LIMIT` media the filter is no longer narrowing enough to beat
  the index, so the index path stays; one `count_scoped_media` query picks between them and
  short-circuits an empty scope before an embedding is ever computed. No dataset a test can seed
  makes the planner choose the index path, so `test_scoped_vector_cte_is_materialized` pins the
  keyword rather than the behaviour.

Two more rules came with the MCP tools, which call `get_hybrid_search_results` with filters the UI
never sends (`channels`, `released_from`/`released_to`, `title_contains`, a separate `fts_text`):
- **New filters belong in `_build_media_conditions`, i.e. in the scope, never in an outer `WHERE`**
  on the search query. Only the scope reaches the `MATERIALIZED` exact path; an outer filter is
  exactly the post-filter that silently returns too few rows. `channels=[]` deliberately matches
  nothing, so a caller whose names all failed to resolve can't get the whole library back.
- **`semantic_cache`'s key is a hand-written tuple: every parameter that changes the result must be
  in it**, or a call differing only in that parameter is served another call's hits. The cache also
  returns its own list object, so callers must copy, never annotate hits in place.

## Transcript-only media in queries

Why `TRANSCRIPT` is a media type, and the supersede rule's purpose, are in the root `AGENTS.md`. How
the queries carry it:

- **`_build_media_conditions` takes `media_types` and `has_transcript`.**
  - The list, `/groups` and `/stats` routers default `media_types` to `LIBRARY_MEDIA_TYPES` through
    `_media_types_filter`. The transcript search scope and MCP pass none, so transcript-only rows stay
    searchable. Don't default it inside the builder, or search loses them.
  - `has_transcript=true` is the Transcripts view: every status, with `include_owned`, so owner-kept
    DELETED transcripts show — the same widened access the search scope uses.
- **`_not_superseded_condition` joins every *user-scoped* query** (list, groups, stats, the search
  scope).
  - It reads the full row's `owner_id` through an alias (`media_details_1`).
    `test_include_owned_widens_access_to_owned_media` therefore asserts on `media_details.owner_id`
    specifically, not on any `owner_id`.
  - The admin all-users view (`user_id` None) deliberately skips it. That keeps the unscoped search
    SQL byte-identical, so it stays on the HNSW index path, and that view shows both rows.
  - Its batched form for the subscription filter is `sync_get_urls_with_full_transcript`; the router's
    single-URL check is `has_full_transcript`. All three must agree on what counts: a non-TRANSCRIPT
    row with transcript blocks that the user can see, by owner or `MediaAccess`.
- **A thumbnail lives as long as the media file or the transcript does.** One with no file left
  is kept under `{audio_path}/.thumbnails/`, named by `kept_thumbnail_path(url, media_type)` — the
  same key as `uq_media_details_url_type`, so a deleted video and a transcript-only item of one URL
  never share a file. The paths that enforce it:
  - A soft delete keeping a transcript that exists *moves* the sidecar there before
    `delete_file(..., cleanup_sidecars=True)` would take it. Any other soft delete deletes it.
  - `DELETE /{id}/transcripts` on a row with no `file_path` deletes it, and so does a hard delete.
  - `cleanup_orphaned_thumbnails` sweeps old files no `thumbnail_path` references: a crash between
    a move and its row write, or a row deleted by URL.
- **Library stats leave transcript-only items out** (`_complete_library_media()` in
  `repositories/stats.py`). The transcription-coverage figures keep every `COMPLETE` row, because
  transcript-only items are in their numerator, and dropping them from the denominator alone could
  push coverage past 100%.

## Embeddings (`services/embeddings.py`)

`services/embeddings.py` — `OnnxEmbedder`: all-MiniLM-L6-v2 on onnxruntime, replacing sentence-transformers so
**torch is not a dependency at all** (~1 GB off the venv). onnxruntime/tokenizers/huggingface-hub were
already present for faster-whisper's Silero VAD. Reproduces the sentence-transformers pipeline exactly
— transformer → attention-masked mean pool → L2 normalize — so vectors stay interchangeable with those
written by the old torch build (measured: max component delta 2.3e-07, cosine 0.9999999, identical
result ordering). Two things are load-bearing: `max_seq_length` comes from the repo's
`sentence_bert_config.json` (**256**, not the tokenizer's 512 — getting it wrong silently shifts
embeddings), and the ONNX feed dict is filtered by `session.get_inputs()` since exports differ on
whether `token_type_ids` is declared. `resolve_model_repo` hard-rejects any model but
all-MiniLM-L6-v2; a different model means a different vector space, so allowing one would silently
invalidate every stored embedding.

## Password recovery

No email integration exists — every path is trust- or filesystem-based.

- **Session invalidation** is the load-bearing mechanism: `create_jwt_token` stamps an `iat` and the middleware drops any token whose `iat` predates `User.password_changed_at`. That `iat` carries **sub-second precision on purpose** — the change/reset endpoints write `password_changed_at` and then immediately mint the caller's replacement cookie, so whole-second granularity would make the replacement look contemporaneous with the tokens it must displace (and it did: a test caught exactly this). `password_changed_at` starts NULL, so tokens predating the feature survive until that user's next password change.
- **`must_change_password`** is enforced in `get_required_user_id` *and* `get_admin_user_id`, mirroring the `is_approved` gate. Consequence: `/auth/me/change-password` cannot use those dependencies — it reads `request.state.user_id` directly, since it's the endpoint a locked user needs in order to clear the flag.
- **The invariant that decides who gets flagged: a password *generated for* someone else is always temporary; a password someone *chose themselves* never is.** So `POST /users/{id}/reset-password` always sets the flag (it takes no body — there is deliberately no opt-out, and a stale client sending the old `require_change` field can't resurrect one), while `/auth/me/change-password`, `/auth/admin-recovery/complete` and the CLI script clear it. Don't add a "let them keep it" path: it would leave the admin holding a working credential for another user's account indefinitely, which is the whole thing this prevents.
- **Admin recovery** (`services/admin_recovery.py`) writes a single-use code to `/data/admin-recovery.txt` (host `./data/`, the same mount as `BACKGROUNDS_DIR`). The file holds a **code, not an already-applied password** — a live credential there would let any anonymous caller lock the admin out on demand. Requesting while an unexpired code exists is a no-op, so repeat calls can't invalidate a code the admin is mid-way through fetching. A `PermissionError` surfaces the `chown 1000:1000 data/` hint, and because the file write precedes the DB update, a failure leaves no half-issued code.
- Both unauthenticated endpoints (`/auth/forgot-password`, `/auth/admin-recovery/request`) return an identical response for unknown, non-admin, and valid usernames — they must not become username/admin oracles.
- **CLI fallback**: `backend/app/scripts/reset_password.py`, run as `task admin:reset-password -- <user>`. It lives under `backend/app/` because the prod image copies only that directory (`Dockerfile.prod`); the pre-existing `backend/scripts/` dir is bind-mounted in dev and **does not exist in prod**.

## Migrations

**Adding an enum value takes two revision files.** Postgres rejects DDL/DML referencing a
value added in the same transaction, and `migrations/env.py` sets
`transaction_per_migration=True` precisely so a later revision *can* use it. Put the
`ALTER TYPE` alone in one file and everything that names the value in the next.

**The schema starts at `baseline_schema`; every later revision in `migrations/versions/` is a delta on it.**
Most of it is autogenerated from SQLModel metadata; the block at the end of `upgrade()` is not,
because `--autogenerate` cannot see any of it — the `vector` extension, the `transcript_embeddings`
table and its HNSW index, `transcript_blocks.text_search` (a generated tsvector column) and its GIN
index, the standalone `task_queue_sequence`, and the seeded `app_settings` row. Alembic will
actively propose **dropping** the four schema objects there, since they are absent from
`SQLModel.metadata`. Anything added to that block needs a matching drop in `downgrade()` by hand.
One more autogenerate quirk to expect: it renders `sqlmodel.sql.sqltypes.AutoString()` for string
columns without emitting `import sqlmodel`, so a freshly generated revision `NameError`s until you
add it.

**Every foreign key's `ondelete` must be declared on the model, not just in a migration.**
`tests/conftest.py` builds the test schema with `SQLModel.metadata.create_all`, so a cascade that
exists only because some migration wrote it is absent from every test database — production deletes
cascade, test deletes raise. `models.py` carries `ondelete=` on all 31 FKs, which is also what keeps
the autogenerated baseline in step with the live schema.

**Server defaults are deliberately almost absent** — only `task_records.retry_count` has one. A
`server_default` is what `ADD COLUMN ... NOT NULL` needs to backfill existing rows; it is not a design
choice worth carrying. SQLModel propagates `Field(default=)` to the SQLAlchemy column as
a *Python-side* default, which both the ORM and Core `insert()` apply to omitted columns, so nothing
in the app depends on the database supplying one. Adding `server_default` would duplicate every
default in two places that must then be kept in sync.

## The seeded `app_settings` row

**Runtime settings** live in the single-row `app_settings` table (see the `AppSettings` model) and are
edited in the Settings UI, taking effect on the next task execution without a restart. **The row is
INSERTed by `baseline_schema.py`, so the model's field defaults never execute for this table** — the
migration is the source of truth for what a live install actually runs, and nothing keeps the two in
step on its own. `tests/test_migration_defaults.py` runs the migration against a scratch database and
asserts the seeded row equals `APP_SETTINGS_DEFAULTS`, column by column over `tuple(APP_SETTINGS_DEFAULTS)`
— so a new column needs its value in the migration *and* in that constant, or the test fails. That is
also why the seed uses literals rather than importing the constant: importing it would make the test
compare the constant against itself. Note the test runs `upgrade head`, not just the baseline, so a
later revision that rewrites a seeded column (`realign_player_clients`) has to agree with the constant
too — and changing a default that existing installs must also pick up takes **both** an edited baseline
seed and a data migration, since the baseline only ever runs on a fresh database.
