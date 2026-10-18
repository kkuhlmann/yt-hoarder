"""The ASGI endpoint behind /mcp: bearer-token auth in front of the SDK's request handler.

Registered as two exact routes rather than a Mount: a Mount only matches below its path,
so a bare `/api/mcp` would fall through to the SPA catch-all and 405. And it authenticates
by itself instead of through the SDK's TokenVerifier, which needs an OAuth issuer and
advertises protected-resource metadata, sending clients off to an OAuth server that does
not exist.
"""

import math
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from config import settings
from mcp_server.context import McpPrincipal, acting_as
from mcp_server.links import resolve_base_url
from rate_limit import MCP_AUTH_FAIL_LIMITER, MCP_TOKEN_LIMITER, client_key
from repositories import access_tokens as token_repo

if TYPE_CHECKING:
    from mcp.server import MCPServer

_UNAUTHORIZED_HEADERS = {'WWW-Authenticate': 'Bearer realm="ytdl-hoarder", error="invalid_token"'}


def bearer_token(header: str | None) -> str | None:
    """The token from an `Authorization: Bearer ytdlh_…` header, or None.

    Anything not carrying the token prefix is rejected here, before it costs a lookup.
    """
    if not header:
        return None
    scheme, _, value = header.partition(' ')
    value = value.strip()
    if scheme.lower() != 'bearer' or not value.startswith(token_repo.TOKEN_PREFIX):
        return None
    return value


def _too_many(wait: float) -> JSONResponse:
    return JSONResponse(
        {'detail': 'Too many requests. Please try again later.'},
        status_code=429,
        headers={'Retry-After': str(max(1, math.ceil(wait)))},
    )


class McpEndpoint:
    """Authenticates a request to a token's user, then lets the MCP server answer it.

    Reads only the Authorization header. The auth middleware has already run for the
    cookie, but a cookie must not authenticate MCP: that would let any page the user
    visits drive these tools with their session.
    """

    def __init__(self, *, dev_frontend: bool) -> None:
        self.dev_frontend = dev_frontend
        # Set by the lifespan when mcp.enabled; None means the endpoint does not exist.
        self.server: MCPServer | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not settings.mcp.enabled or self.server is None:
            await JSONResponse({'detail': 'Not found'}, status_code=404)(scope, receive, send)
            return

        request = Request(scope)
        address = client_key(request)
        wait = MCP_AUTH_FAIL_LIMITER.peek(address)
        if wait is not None:
            await _too_many(wait)(scope, receive, send)
            return

        raw = bearer_token(request.headers.get('authorization'))
        resolved = await token_repo.resolve_token(raw) if raw else None
        if resolved is None:
            MCP_AUTH_FAIL_LIMITER.retry_after(address)
            response = JSONResponse(
                {'detail': 'A valid personal access token is required.'},
                status_code=401,
                headers=_UNAUTHORIZED_HEADERS,
            )
            await response(scope, receive, send)
            return

        wait = MCP_TOKEN_LIMITER.retry_after(f'token:{resolved.token.id}')
        if wait is not None:
            await _too_many(wait)(scope, receive, send)
            return

        principal = McpPrincipal(
            user_id=resolved.user.id,
            username=resolved.user.username,
            token_id=resolved.token.id,
            base_url=resolve_base_url(
                scope.get('scheme', 'http'),
                request.headers.get('host'),
                settings.mcp.public_url,
                self.dev_frontend,
            ),
        )
        with acting_as(principal):
            await self.server.session_manager.handle_request(scope, receive, send)
