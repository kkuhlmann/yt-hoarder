"""FastAPI dependencies for authentication enforcement.

These are used with Depends() in route handlers to enforce auth requirements.
"""

import math
from dataclasses import dataclass
from functools import partial

from fastapi import Depends, HTTPException, Request, status

from models import TokenScope
from rate_limit import (
    EXTENSION_AUTH_FAIL_LIMITER,
    EXTENSION_POLL_LIMITER,
    EXTENSION_TOKEN_LIMITER,
    SlidingWindowLimiter,
    client_key,
)
from repositories import access_tokens as token_repo
from repositories import clip_access as ca_repo
from repositories import clips as clips_repo
from repositories import media_access as ma_repo
from repositories import media_details as md_repo
from repositories import playlist_access as pa_repo
from repositories import playlists as playlists_repo
from repositories.access_tokens import ResolvedToken


def get_required_user_id(request: Request) -> int:
    """Return user_id or raise 401/403. Use for endpoints that require auth.

    Rejects unapproved accounts with 403: the admin-approval gate is enforced here,
    server-side, not just in the frontend. `is_approved` is resolved from the DB by
    the auth middleware, so revoking approval takes effect on the next request. A
    pending user still holds a valid token (so /me can report their pending state)
    but cannot reach any data endpoint.

    The same applies to a user holding an admin-issued temporary password: they are
    authenticated, but locked out of data endpoints until they choose a new one.
    """
    user_id = request.state.user_id
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Authentication required',
        )
    if not request.state.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Account pending admin approval',
        )
    if request.state.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Password change required',
        )
    return user_id


def _too_many_requests(wait: float) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail='Too many requests. Please try again later.',
        headers={'Retry-After': str(max(1, math.ceil(wait)))},
    )


async def _authenticate_extension_token(
    request: Request,
    raw: str | None,
    limiter: SlidingWindowLimiter = EXTENSION_TOKEN_LIMITER,
) -> ResolvedToken:
    address = client_key(request)
    if (wait := EXTENSION_AUTH_FAIL_LIMITER.peek(address)) is not None:
        raise _too_many_requests(wait)

    resolved = await token_repo.resolve_token(raw, TokenScope.EXTENSION) if raw else None
    if resolved is None:
        EXTENSION_AUTH_FAIL_LIMITER.retry_after(address)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='A valid browser-extension token is required.',
            headers={'WWW-Authenticate': token_repo.BEARER_CHALLENGE},
        )

    if (wait := limiter.retry_after(f'token:{resolved.token.id}')) is not None:
        raise _too_many_requests(wait)

    # The middleware filled request.state from whatever cookie came along. Replace it, so
    # anything downstream that reads it sees the token's user, and never an admin view.
    user = resolved.user
    request.state.user_id = user.id
    request.state.username = user.username
    request.state.is_admin = False
    request.state.is_approved = user.is_approved
    request.state.must_change_password = user.must_change_password
    return resolved


async def get_extension_token(request: Request) -> ResolvedToken:
    """The extension-scoped token this request carries, or raise 401/429.

    Only the Authorization header counts, never the cookie: this backs the extension's
    connection test, which must pass only for what the extension itself will send.
    """
    raw = token_repo.bearer_token(request.headers.get('authorization'))
    return await _authenticate_extension_token(request, raw)


async def get_extension_poll_token(request: Request) -> ResolvedToken:
    """`get_extension_token` charged to the polling budget instead of the submit one.

    For the task list the popup refreshes every few seconds. Failed authentications still
    count against the shared per-address limiter, so a client must stop on its first 401.
    """
    raw = token_repo.bearer_token(request.headers.get('authorization'))
    return await _authenticate_extension_token(request, raw, EXTENSION_POLL_LIMITER)


@dataclass(frozen=True)
class CookieOrExtensionCaller:
    user_id: int
    via_extension_token: bool


async def get_cookie_or_extension_caller(request: Request) -> CookieOrExtensionCaller:
    """The user behind an extension token when one is presented, else the cookie's.

    For the add-only routes the extension calls, and nothing else. A `Bearer ytdlh_…`
    header commits the request to the token: an invalid one is a 401 even beside a valid
    cookie. Any other Authorization header is ignored, so a proxy adding Basic auth in
    front of the app leaves the web UI's cookie path working.
    """
    raw = token_repo.bearer_token(request.headers.get('authorization'))
    if raw is None:
        return CookieOrExtensionCaller(get_required_user_id(request), via_extension_token=False)
    resolved = await _authenticate_extension_token(request, raw)
    return CookieOrExtensionCaller(resolved.user.id, via_extension_token=True)


async def get_cookie_or_extension_user_id(
    caller: CookieOrExtensionCaller = Depends(get_cookie_or_extension_caller),
) -> int:
    """`get_cookie_or_extension_caller` for a route that treats both callers alike."""
    return caller.user_id


def get_admin_user_id(request: Request) -> int:
    """Return user_id or raise 401/403. Use for admin-only endpoints.

    `is_admin` is resolved from the DB by the auth middleware, so a demoted or deleted
    admin loses access on their next request rather than at token expiry.
    """
    user_id = request.state.user_id
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Authentication required',
        )
    if not request.state.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Admin access required',
        )
    if request.state.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Password change required',
        )
    return user_id


def get_effective_user_id(
    request: Request,
    admin_view: bool = False,
    user_id: int = Depends(get_required_user_id),
) -> int | None:
    """The query-layer access filter: None means no user filter (admin sees all)."""
    return None if (admin_view and request.state.is_admin) else user_id


def get_admin_override(request: Request, admin_view: bool = False) -> bool:
    """True when the caller is an admin who explicitly asked for the admin view."""
    return admin_view and request.state.is_admin


async def get_entity_or_404(
    fetch_fn,
    entity_id: int | str,
    entity_label: str,
    access_check=None,
):
    """Fetch an entity by ID, raise 404 if missing, then run optional access check.

    Args:
        fetch_fn: Async callable that takes entity_id and returns entity or None.
        entity_id: The ID to look up.
        entity_label: Human-readable name for error messages (e.g. 'Clip', 'Playlist').
        access_check: Optional async callable(entity) that raises HTTPException on failure.
    """
    entity = await fetch_fn(entity_id)
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'{entity_label} with id {entity_id} not found',
        )
    if access_check:
        await access_check(entity)
    return entity


def entity_access_dependency(fetch_fn, entity_label: str, check_fn):
    """Build a Depends() that fetches /{id}, 404s if missing, and runs the access check.

    entity_label is part of the 404 detail and differs per router ('Media' vs
    'MediaDetails') — preserve each router's existing label exactly.
    """

    async def dependency(request: Request, id: int, user_id: int = Depends(get_required_user_id)):
        return await get_entity_or_404(
            fetch_fn,
            id,
            entity_label,
            access_check=partial(check_fn, user_id, is_admin=request.state.is_admin),
        )

    return dependency


get_accessible_media = entity_access_dependency(
    md_repo.get_media_details_by_id, 'Media', ma_repo.check_access_or_raise
)
get_accessible_media_details = entity_access_dependency(
    md_repo.get_media_details_by_id, 'MediaDetails', ma_repo.check_access_or_raise
)
get_accessible_clip = entity_access_dependency(
    clips_repo.get_clip_by_id, 'Clip', ca_repo.check_clip_access_or_raise
)
get_accessible_playlist = entity_access_dependency(
    playlists_repo.get_playlist_by_id, 'Playlist', pa_repo.check_playlist_access_or_raise
)
