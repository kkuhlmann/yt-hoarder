import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import DEFAULT_INSECURE_SECRET_KEY, settings
from database import db
from logger import logger
from mcp_server.http import McpEndpoint
from middleware.auth import OptionalAuthMiddleware
from orchestrator import orch
from orchestrator.recovery import retry_scheduler_loop, run_startup_recovery
from orchestrator.scheduler import cron_loop
from progress_publisher import broadcaster
from repositories import settings as settings_repo
from repositories.errors import InvalidStateError, NotFoundError
from routers.auth import router as auth_router
from routers.clips import router as clips_router
from routers.media import router as media_router
from routers.media_details import router as media_details_router
from routers.playlists import router as playlists_router
from routers.settings import router as settings_router
from routers.sse import router as sse_router
from routers.stats import router as stats_router
from routers.subscriptions import router as subscriptions_router
from routers.task_records import router as tasks_router
from routers.tokens import router as tokens_router
from routers.ytdl_router import router as ytdl_router
from tasks import register_all_jobs
from tasks.registry import build_cron_jobs
from utils import load_embedding_model

STATIC_DIR = Path(os.getenv('STATIC_FILES_DIR', '/app/static'))
SERVE_FRONTEND = STATIC_DIR.exists() and (STATIC_DIR / 'index.html').exists()

API_PREFIX = '/api' if SERVE_FRONTEND else ''

# Spelled out rather than derived from API_PREFIX: the catch-all exists only when
# the frontend is served, which is exactly when the prefix is '/api', and deriving
# it would make the guard match every path in the dev config, where it is ''.
SPA_RESERVED_PREFIX = 'api'
# Nothing is served from here, and an MCP client that gets a 401 probes it for OAuth
# metadata (RFC 9728): answering with index.html and a 200 reads as broken metadata.
WELL_KNOWN_PREFIX = '.well-known'

mcp_endpoint = McpEndpoint(dev_frontend=not SERVE_FRONTEND)


def docs_kwargs(serve_frontend: bool) -> dict[str, str | None]:
    """OpenAPI schema and its two UIs are dev-only; the production image never registers them.

    Returns FastAPI constructor kwargs. In prod the routes simply don't exist, so `/docs`
    falls through to the SPA catch-all like any other unknown path.
    """
    if serve_frontend:
        return {'openapi_url': None, 'docs_url': None, 'redoc_url': None}
    return {}


def cors_kwargs(origins: list[str]) -> dict[str, object]:
    """CORSMiddleware kwargs for dev mode, where an empty allowlist means any origin.

    Dev serves the UI on :3000 and the API on :8000, so every call is cross-origin and
    the address the browser used is not knowable here — localhost, a LAN IP, a Tailscale
    name. With no origins configured the regex echoes whatever arrives (and sets `Vary:
    Origin`), which is what makes the dev UI work from any device unconfigured. Listing
    origins restores a strict allowlist.
    """
    return {
        'allow_origins': origins,
        'allow_origin_regex': None if origins else '.*',
        'allow_credentials': True,
        'allow_methods': ['*'],
        'allow_headers': ['*'],
    }


BANNER = r"""
     _______________/\/\____________/\/\__/\/\______________/\/\__________________________________________________/\/\_________________________
    _/\/\__/\/\__/\/\/\/\/\________/\/\__/\/\______________/\/\__________/\/\/\____/\/\/\______/\/\__/\/\________/\/\____/\/\/\____/\/\__/\/\_
   _/\/\__/\/\____/\/\________/\/\/\/\__/\/\____/\/\/\/\__/\/\/\/\____/\/\__/\/\______/\/\____/\/\/\/\______/\/\/\/\__/\/\/\/\/\__/\/\/\/\___
  ___/\/\/\/\____/\/\______/\/\__/\/\__/\/\______________/\/\__/\/\__/\/\__/\/\__/\/\/\/\____/\/\________/\/\__/\/\__/\/\________/\/\_______
 _______/\/\____/\/\/\______/\/\/\/\__/\/\/\____________/\/\__/\/\____/\/\/\____/\/\/\/\/\__/\/\__________/\/\/\/\____/\/\/\/\__/\/\_______
_/\/\/\/\_________________________________________________________________________________________________________________________________
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(BANNER)  # noqa: T201 — startup banner, deliberately raw stdout, not a log record

    if settings.auth.secret_key == DEFAULT_INSECURE_SECRET_KEY:
        msg = (
            'auth.secret_key is still the sample value, which is published in this '
            'repo — anyone could forge an admin token. Set a real one as '
            'auth.secret_key in config.yml (or, outside Docker, via the '
            'AUTH__SECRET_KEY environment variable). '
            'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
        )
        raise RuntimeError(msg)

    db.initialize_database()

    app.state.embedding_model = load_embedding_model()

    register_all_jobs()
    broadcaster.set_loop(asyncio.get_running_loop())
    app_settings = await settings_repo.get_settings()
    await orch.start(settings_repo.lane_concurrency(app_settings))

    await asyncio.to_thread(run_startup_recovery, orch, settings.tasks.purge_on_startup)

    orch.add_service(retry_scheduler_loop(orch), 'retry-scheduler')
    orch.add_service(
        cron_loop(build_cron_jobs(app_settings.subscription_check_minutes), lambda: orch.running),
        'cron-scheduler',
    )

    if SERVE_FRONTEND:
        logger.info(f'Production mode: serving frontend from {STATIC_DIR}')
        logger.info(f'API routes prefixed with {API_PREFIX}')
    else:
        logger.info('Development mode: API-only, no frontend serving')

    async with AsyncExitStack() as stack:
        if settings.mcp.enabled:
            # Imported here so a disabled install never loads the SDK.
            from mcp_server.server import build_mcp_server

            server = build_mcp_server()
            # A sub-app's lifespan never runs, so its session manager starts here.
            await stack.enter_async_context(server.session_manager.run())
            mcp_endpoint.server = server
            logger.info(f'MCP server enabled at {API_PREFIX}/mcp')

        yield

        logger.info('Shutting down application.')
        mcp_endpoint.server = None

    await orch.stop()
    await db.close()


app = FastAPI(lifespan=lifespan, **docs_kwargs(SERVE_FRONTEND))


# Repositories raise transport-agnostic errors; the HTTP status lives here, at the edge.
@app.exception_handler(NotFoundError)
async def _not_found_error_handler(_request, exc):
    return JSONResponse(status_code=404, content={'detail': str(exc)})


@app.exception_handler(InvalidStateError)
async def _invalid_state_error_handler(_request, exc):
    return JSONResponse(status_code=400, content={'detail': str(exc)})


# Auth middleware — reads JWT cookie, never rejects (enforcement is per-route via dependencies)
app.add_middleware(OptionalAuthMiddleware)

app.include_router(auth_router, tags=['auth'], prefix=f'{API_PREFIX}/auth')
app.include_router(tokens_router, tags=['auth'], prefix=f'{API_PREFIX}/auth/tokens')
app.include_router(
    subscriptions_router, tags=['subscriptions'], prefix=f'{API_PREFIX}/subscriptions'
)
app.include_router(
    media_details_router, tags=['media details'], prefix=f'{API_PREFIX}/media-details'
)
app.include_router(tasks_router, tags=['tasks'], prefix=f'{API_PREFIX}/tasks')
app.include_router(ytdl_router, tags=['YouTube download'], prefix=f'{API_PREFIX}/ytdl')
app.include_router(media_router, tags=['media'], prefix=f'{API_PREFIX}/media')
app.include_router(clips_router, tags=['clips'], prefix=f'{API_PREFIX}/clips')
app.include_router(playlists_router, tags=['playlists'], prefix=f'{API_PREFIX}/playlists')
app.include_router(sse_router, tags=['sse'], prefix=f'{API_PREFIX}/sse')
app.include_router(settings_router, tags=['settings'], prefix=f'{API_PREFIX}/settings')
app.include_router(stats_router, tags=['stats'], prefix=f'{API_PREFIX}/stats')
# Always registered, answering 404 while mcp.enabled is off, so it is indistinguishable
# from an unknown path then. Both spellings, ahead of the SPA catch-all below.
app.add_route(f'{API_PREFIX}/mcp', mcp_endpoint, include_in_schema=False)
app.add_route(f'{API_PREFIX}/mcp/', mcp_endpoint, include_in_schema=False)


@app.get('/health')
def healthcheck():
    return {'status': 'ok'}


def resolve_spa_file(path: str) -> Path | None:
    """Map a request path to a file to serve, defaulting to the SPA entrypoint.

    Returns None for anything under the API prefix or `.well-known/`, which the
    caller turns into a 404. Without that an unmatched `/api/*` falls through to
    index.html and an unknown endpoint answers 200 with a page of HTML, which a
    client cannot tell from a real response — a service worker deciding whether a
    reply is cacheable least of all.

    Containment must be re-checked *after* resolving: uvicorn percent-decodes the
    request path before routing and Starlette's `:path` convertor matches `..`, so
    the raw join escapes STATIC_DIR (`/%2e%2e/%2e%2e/etc/app/config.yml`). Resolving
    also collapses symlinks that point outside the static root.
    """
    for prefix in (SPA_RESERVED_PREFIX, WELL_KNOWN_PREFIX):
        if path == prefix or path.startswith(f'{prefix}/'):
            return None

    static_root = STATIC_DIR.resolve()
    index = static_root / 'index.html'

    try:
        candidate = (static_root / path).resolve()
    except (OSError, ValueError):
        # ValueError, not OSError, is what an embedded NUL raises out of realpath().
        return index

    if not candidate.is_relative_to(static_root):
        return index

    if candidate.is_file():
        return candidate

    # Next.js trailingSlash: true emits directories containing index.html
    if (candidate / 'index.html').is_file():
        return candidate / 'index.html'

    return index


if SERVE_FRONTEND:
    if (STATIC_DIR / '_next').exists():
        app.mount('/_next', StaticFiles(directory=str(STATIC_DIR / '_next')), name='next-static')

    # Catch-all route for SPA - must be after API routes
    @app.get('/{path:path}')
    async def serve_spa(path: str):
        target = resolve_spa_file(path)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Not found')
        return FileResponse(target)
else:
    # Dev only: the production image serves the frontend same-origin and never
    # registers CORS at all. What actually keeps a foreign site from reading the
    # API is the auth cookie's SameSite=lax (routers/auth.py), which withholds
    # credentials from a cross-site fetch no matter what CORS permits — so the
    # default here is open and auth.allowed_origins is a hardening option rather
    # than the thing holding the door shut.
    app.add_middleware(CORSMiddleware, **cors_kwargs(settings.auth.allowed_origins))
