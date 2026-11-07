---
paths:
  - "backend/app/mcp_server/**/*"
  - "backend/app/repositories/access_tokens.py"
  - "backend/app/routers/tokens.py"
  - "backend/app/routers/extension.py"
  - "backend/tests/test_mcp_*.py"
  - "backend/tests/routers/test_extension_tokens.py"
  - "backend/tests/routers/test_tokens.py"
---

# MCP server and personal access tokens

Root `AGENTS.md` carries the one-line rule: each token scope opens one surface, and the cookie never
authenticates MCP. User-facing setup is in `docs/CONFIGURATION.md` → *AI agents (MCP)* and
`docs/EXTENSION.md`. This is what you need before changing the endpoint, the tools or the token rules.

## The endpoint (`mcp_server/http.py`, wired in `main.py`)

- **`stateless_http=True` is a security requirement, not tuning.** In session mode the SDK starts each
  session's server task from the request that sent `initialize`, so contextvars — including the
  `McpPrincipal` the tools read — freeze at that caller. Any later request reusing the session id
  would run tools as the first user. Stateless starts a task per request from that request's context.
  `tests/test_mcp_http.py::test_concurrent_requests_never_cross_users` pins it; keep it green.
  (Modern 2026-07-28 clients are sessionless regardless; the flag governs legacy clients.)
- **Two exact `Route`s, never a `Mount`.** A Mount compiles to `path + "/{path:path}"`, so a bare
  `/api/mcp` would miss it, fall to the SPA catch-all's partial match and 405 in prod. The endpoint
  is an ASGI class instance, which Starlette treats as a raw app accepting every method.
- **Always registered, 404 while `mcp.enabled` is off**, so tests can flip it with monkeypatch and the
  disabled install looks like any unknown path. The SDK is imported only in the lifespan branch that
  starts it (`from mcp_server.server import build_mcp_server`), so a disabled install never loads it.
  A mounted sub-app's lifespan never runs, which is why `session_manager.run()` is entered there.
- **Our own bearer check, not the SDK's `TokenVerifier`.** The SDK's auth requires an `issuer_url`
  and advertises RFC 9728 protected-resource metadata in its 401, sending clients looking for an
  OAuth server that doesn't exist. Ours sends a plain `WWW-Authenticate: Bearer realm=…`. For the same
  reason `resolve_spa_file` refuses `.well-known/`: a client probing it after a 401 must get a 404,
  not the SPA shell with a 200. If OAuth is ever added, that is the point to move onto the SDK's auth.
- **Only the `Authorization` header authenticates.** `OptionalAuthMiddleware` still runs for this
  route and fills `request.state` from any cookie; the endpoint ignores it. Letting the cookie count
  would let any page the user visits drive the tools with their session.
- **DNS-rebinding protection is off on purpose.** The SDK's default Host allowlist is localhost-only
  and answers 421 to every LAN or proxied address. Rebinding attacks need a browser carrying ambient
  credentials; this endpoint takes only an explicit bearer token and prod sends no CORS.
- **`MCPServer()` calls `logging.basicConfig()`.** The app's root logger has no handlers, so that call
  would attach one and print every app log line twice. `build_mcp_server` saves and restores the root
  logger's handlers and level around construction; a test pins it.

## Tokens (`repositories/access_tokens.py`)

- **Stored as SHA-256, not bcrypt.** A token carries 32 random bytes, so a fast hash is safe, and
  bcrypt's ~250 ms would be paid on every MCP call. The `ytdlh_` prefix lets the endpoint reject junk
  before touching the database.
- **`token_status()` is the single validity rule**, shared by auth and the token list so the UI can
  say *why* a token stopped working. `invalidated` = created before `User.password_changed_at`, which
  mirrors the cookie `iat` check: every password path already writes that column, so none of them has
  to know tokens exist. Don't add per-path revocation calls; extend `token_status` instead.
- **Revoking deletes the row.** No `revoked_at`, so the per-user cap counts real rows.
- **`last_used_at` is written at most once a minute** through a guarded `UPDATE … WHERE last_used_at
  < now - 60s`, so concurrent calls don't race or write on every request.

## Scopes, and the extension's REST path

- **`PersonalAccessToken.scope` (`mcp` | `extension`) is part of `resolve_token`'s WHERE clause**, not
  a check after it. `resolve_token` stamps `last_used_at`, so a check afterwards would mark a token
  used on a surface it can't open. A wrong-scope token is simply not found. Scope is not a
  `TokenStatus`: it says where a token works, not whether it works.
- **The column has no default, on the model or in the database.** Migration `add_token_scope`
  backfilled the pre-scope rows as `MCP` with a server default, then dropped it. `TokenCreateRequest`
  defaults to `mcp` only because that is all a client predating scopes could have meant.
- **Only MCP-scope creation is gated on `mcp.enabled`** (404). Extension tokens are creatable with MCP
  off, which is also why the token menu is no longer gated on `mcp_enabled`.
- **A third scope needs two migration files.** `tokenscope` is a native Postgres enum, and `ALTER TYPE
  … ADD VALUE` can't be used in the same transaction (`backend-domains.md` → Migrations).
- **The extension authenticates in route dependencies (`dependencies.py`), never in
  `OptionalAuthMiddleware`.**
  - `get_cookie_or_extension_caller` backs `POST /ytdl/` and `POST /subscriptions`, the only routes
    the web UI and the extension share. It says whether the call came by token;
    `get_cookie_or_extension_user_id` is the same dependency for a route that doesn't care.
  - `get_extension_token` backs `GET /extension/me` and is token-only, so the connection test passes
    only for what the extension really sends.
  - `get_extension_poll_token` is `get_extension_token` charged to `EXTENSION_POLL_LIMITER`. It backs
    `GET /extension/tasks`, which the open popup polls every few seconds.
  - Adding either to another route widens what an extension token can do. The parametrized "opens
    nothing else" test in `test_extension_tokens.py` should grow with any route that adopts one.
- **Overwrite is the one destructive thing an extension token can do, and only on a single
  download.** `POST /subscriptions` 403s `overwrite: true` from a token. On a subscription,
  overwrite makes every tick delete and re-download each video of the channel the user already has
  (`_handle_existing_media`), and a token can't edit the subscription to stop it. A download's
  overwrite replaces the one copy the user just asked for again.
- **A `Bearer ytdlh_…` header commits a request to the token path.** An invalid token is a 401 even
  with a valid cookie beside it. Any other `Authorization` value is ignored rather than refused, so a
  proxy adding `Basic` auth in front of the app keeps the web UI's cookie path working.
  `bearer_token()` lives in `access_tokens.py` so both paths parse the header identically.
- **The token path overwrites `request.state`**, setting `user_id`, `is_admin=False` and the rest from
  the token's user, so nothing downstream sees the cookie's user or an admin view.
- **Its limiters are separate from MCP's.** `EXTENSION_TOKEN_LIMITER` allows 60 calls a minute per
  token, since each call can start a job. `EXTENSION_AUTH_FAIL_LIMITER` counts failures per address.
  A misconfigured agent therefore can't lock the extension out. All are in `ALL_LIMITERS`, which the
  conftest reset relies on.
- **Polling has its own budget, `EXTENSION_POLL_LIMITER`.** At the popup's 3 s cadence it would use
  half of `EXTENSION_TOKEN_LIMITER`, which is the submit allowance. The failure limiter is still
  shared per address, so a client that keeps polling with a refused token could lock out everyone
  behind that address: the popup stops at its first fatal reply, and that is load-bearing.
- **`GET /extension/tasks` is a lean, owner-only feed, not `GET /tasks`.** It calls
  `task_records.get_progress_feed` (two small SELECTs: no count, queue positions or downstream scan)
  with the token user's id, which is typed `int` so there is no admin view to reach by accident.
  Active rows come first in run order (`_run_order`, shared with the Tasks page), then finished ones
  newest first. `TASK_FEED_FINISHED_SLOTS` of the 25 are held for finished rows: under one ranking
  with a single cut, a queued subscription backlog hid the outcome of the download just submitted. The response
  model leaves out `pending_payload`, `download_job_url`, `upstream_task_ids` and `user_id`, and a test
  pins its keys. SPRITE_GENERATION is excluded as a chain chore; so are SKIPPED, CANCELLED and
  UPSTREAM_FAILED. Its timestamps carry an explicit `+00:00`.

## Tools (`mcp_server/server.py`, `formatting.py`)

- **Every tool acts as `current_principal()`, with `is_admin=False` semantics** — own plus shared
  media, never admin view. Search goes through `get_hybrid_search_results(user_id=…,
  rating_user_id=…)`; `get_transcript` checks `ma_repo.user_can_access_media` and answers "missing"
  and "not yours" with the same message so ids can't be probed.
- **Filters that resolve to nothing return nothing.** An unmatched channel or tag means "Nothing was
  searched" plus `unmatched_*`, never a silently widened search. `_build_media_conditions` backs this
  up: `channels=[]` matches no rows.
- **Each tool returns `Annotated[CallToolResult, Model]`**: the model is the validated
  `structured_content`, and `content` is our own compact text. `content` is the only part most agents
  show the model, and the SDK's default for it is indented JSON.
- **Citations carry their links, and every search result ends with the answer format.** A passage
  id renders as `[B69](source link)` because models copy a citation token as they saw it, so inline
  citations bring their evidence along. The footer (`_answer_format`) then shows the evidence-table
  convention with a real row built from the best passage. Instructions further from the passages
  (server instructions, the `research` prompt) were too easy for agents to drop. The footer is phrased
  as "the format for these passages", not as an order, because models rightly treat commands in tool
  output warily; keep it that way.
- **Search hits come from the search cache by reference.** `merge_adjacent_hits` reads them and
  builds new objects; never annotate a hit in place.
- **Responses are capped at ~20k characters** (`RESPONSE_MAX_CHARS`): Claude Code warns above 10k
  tokens of tool output and truncates at 25k.
- **Every page of one search fetches the same `SEARCH_FETCH_DEPTH` hits**, whatever its `offset`;
  the page is a slice of the merged passages. Never pass `offset` or `limit` through to
  `get_hybrid_search_results`.
  - **Determinism:** merging is greedy per video, so merging a different number of hits regroups
    passages near the edge, and pages would repeat or skip some.
  - **One cache entry:** with a fixed depth, all pages of a search share one `semantic_cache` entry.
- **Pagers advance by what they rendered.** `next_offset` (search, `list_videos`) and
  `next_start_seconds` (`get_transcript`) count only what fit in the character budget, and the first
  item always renders, so following them always makes progress. `mention_timeline` counts its totals
  before cutting rows, so they stay exact.
- **`get_transcript` places its window by `start_time` only** (`get_blocks_in_window`'s anchor).
  - **Why:** stored blocks written before the aggregator partitioned the timeline overrun their
    successor's start, so an `end_time > start` window would re-read the previous block on every
    page.
  - **Trap:** don't "simplify" it back to an end-time overlap.
- **Keyword syntax is shared.** `keywords` (search) and `terms` (timeline) both go through
  `fts_any_of`: any of the alternatives matches, and a multi-word one matches as a phrase.
  `formatting.keywords_for` turns timeline terms back into search keywords that match exactly the
  same blocks, and a test pins that round trip.
- **"Filters that match nothing search nothing"** applies to `list_videos` and `mention_timeline`
  too. All three go through `_resolve_library_filters`.
- **`media_ids` echo only their count**, never the ids, so a result can't say which of them the
  caller can see.
- **Raise `ToolError` for anything the model should read.** Any other exception reaches the client
  only as "Error executing tool …", which is right for crashes and useless for bad input.

## In-app links (`mcp_server/links.py`)

`app_url` is `{base}/?media=<id>&t=<s>`, handled by `frontend/app/lib/deepLink.ts` and
`useDeepLinkPlayback`. `base` is `mcp.public_url` when set, else the request's scheme plus its `Host`
header. Uvicorn rewrites the scheme for a proxy in `FORWARDED_ALLOW_IPS` but never the Host, so a
Host-rewriting proxy needs `public_url`. In dev, the port is swapped to 3000, because the MCP request
arrives at the API on :8000 while the UI is on :3000. Host values outside a strict hostname pattern
fall back to `localhost` rather than being echoed into a link.

`media_app_url` (`formatting.py`) gives an app link only for a `COMPLETE` item. A transcript-only
item counts: the deep link opens its embed page, which plays it through YouTube beside the
transcript. Deleted media that kept its transcript gets the source link alone, since its row isn't
`COMPLETE`.

## Testing

- **Tool tests** (`test_mcp_tools.py`) use the SDK's in-memory `Client(server)` inside
  `acting_as(principal)`. The client's server tasks are created inside that context, which is how the
  tools see it.
- **HTTP tests** (`test_mcp_http.py`) go through `main.app` with `httpx.ASGITransport`. They enter
  `session_manager.run()` **inside the test** via `serving()`, not in an async fixture: the SDK's
  anyio task group must be exited by the task that entered it, and pytest-asyncio tears fixtures down
  in a different task ("Attempted to exit cancel scope in a different task").
- **A session manager runs once.** Build a fresh server per test (`build_mcp_server(embedder_factory=
  StubEmbedder)`), and point `main.mcp_endpoint.server` at it with monkeypatch.
