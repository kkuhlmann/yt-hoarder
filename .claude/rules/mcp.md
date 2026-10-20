---
paths:
  - "backend/app/mcp_server/**/*"
  - "backend/app/repositories/access_tokens.py"
  - "backend/app/routers/tokens.py"
  - "backend/tests/test_mcp_*.py"
---

# MCP server and personal access tokens

Root `AGENTS.md` carries the one-line rule (tokens authenticate MCP only, the cookie never does).
User-facing setup is in `docs/CONFIGURATION.md` → *AI agents (MCP)*. This is what you need before
changing the endpoint, the tools or the token rules.

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
