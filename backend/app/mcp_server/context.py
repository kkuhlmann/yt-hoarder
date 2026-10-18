"""Who the current MCP request acts as, set by the HTTP endpoint for the tools to read."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class McpPrincipal:
    user_id: int
    username: str
    token_id: int
    # Origin the in-app links in results point at, e.g. 'http://nas:8000'.
    base_url: str


_principal: ContextVar[McpPrincipal | None] = ContextVar('mcp_principal', default=None)


def current_principal() -> McpPrincipal:
    principal = _principal.get()
    if principal is None:
        # Only reachable through a wiring mistake: the endpoint sets it before any
        # request reaches the SDK, so a tool running without it must not guess.
        msg = 'MCP tool called without an authenticated principal'
        raise RuntimeError(msg)
    return principal


@contextmanager
def acting_as(principal: McpPrincipal) -> Iterator[None]:
    token = _principal.set(principal)
    try:
        yield
    finally:
        _principal.reset(token)
