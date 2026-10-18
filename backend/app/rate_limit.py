"""In-process sliding-window rate limiting for the unauthenticated auth endpoints and MCP.

Keyed on client address alone, never on the submitted username. A per-username counter
would let anyone lock a chosen account out of its own sign-in, and would reintroduce the
enumeration oracle that /forgot-password and /admin-recovery/request are written to avoid:
identical response bodies stop telling you whether an account exists, but a per-username
budget starts telling you again.

The limit is charged before the handler runs, so it is reached at the same rate whether or
not the username exists, and a caller flooding these routes never reaches the bcrypt
comparison that makes them expensive to serve.
"""

import math
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request, status

# Bounds the table against a caller rotating source addresses. Keys are refreshed on
# use and dropped least-recent-first, so the client being evicted is never the active one.
MAX_TRACKED_CLIENTS = 4096


class SlidingWindowLimiter:
    """Counts hits per key over a moving time window."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def retry_after(self, key: str, now: float | None = None) -> float | None:
        """Charge one hit against `key`, or return the seconds to wait if it is full.

        Rejected calls are not recorded, so a blocked caller recovers a full window after
        their last *accepted* request instead of extending their own block by retrying.
        """
        now = time.monotonic() if now is None else now
        self._evict(now)

        hits = self._hits.setdefault(key, deque())
        self._hits.move_to_end(key)

        cutoff = now - self.window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= self.limit:
            return hits[0] - cutoff

        hits.append(now)
        return None

    def peek(self, key: str, now: float | None = None) -> float | None:
        """Like retry_after, but never charges a hit.

        For limiters that count only failures: check before doing the work, then charge
        with retry_after once the attempt has actually failed.
        """
        now = time.monotonic() if now is None else now
        hits = self._hits.get(key)
        if not hits:
            return None
        cutoff = now - self.window_seconds
        live = [hit for hit in hits if hit > cutoff]
        if len(live) >= self.limit:
            return live[0] - cutoff
        return None

    def reset(self) -> None:
        self._hits.clear()

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_seconds
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]
        while len(self._hits) >= MAX_TRACKED_CLIENTS:
            self._hits.popitem(last=False)


# Sized so that ordinary use never trips: a mistyped password a few times over, or a
# household behind one NAT address signing in together. Sustained brute force is cut to a
# few attempts a minute, which bcrypt already makes useless.
LOGIN_LIMITER = SlidingWindowLimiter(limit=30, window_seconds=300)
REGISTER_LIMITER = SlidingWindowLimiter(limit=10, window_seconds=3600)

# One shared budget for the whole recovery flow, so burning it on code guesses also stops
# the requests that mint new codes.
RECOVERY_LIMITER = SlidingWindowLimiter(limit=10, window_seconds=3600)

# MCP calls are cheap to make and an agent may fan out several searches per question,
# so the per-token budget is generous; it exists to stop a runaway loop, not normal use.
MCP_TOKEN_LIMITER = SlidingWindowLimiter(limit=120, window_seconds=60)
# Counts only failed MCP authentications, per client address. A 256-bit token cannot be
# guessed, so this is about keeping junk requests off the database, not brute force.
MCP_AUTH_FAIL_LIMITER = SlidingWindowLimiter(limit=30, window_seconds=300)

ALL_LIMITERS = (
    LOGIN_LIMITER,
    REGISTER_LIMITER,
    RECOVERY_LIMITER,
    MCP_TOKEN_LIMITER,
    MCP_AUTH_FAIL_LIMITER,
)


def client_key(request: Request) -> str:
    """Identify the caller for limiting purposes.

    X-Forwarded-For is deliberately ignored — it is caller-controlled, so honouring it
    would hand out a fresh budget per request. Behind a reverse proxy, run uvicorn with
    --proxy-headers and --forwarded-allow-ips so request.client is the real peer; trusting
    the hop belongs in the server's proxy configuration, not here.
    """
    return request.client.host if request.client else 'unknown'


def _rate_limited(limiter: SlidingWindowLimiter):
    async def dependency(request: Request) -> None:
        wait = limiter.retry_after(client_key(request))
        if wait is None:
            return
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail='Too many attempts. Please try again later.',
            headers={'Retry-After': str(max(1, math.ceil(wait)))},
        )

    return dependency


login_rate_limit = _rate_limited(LOGIN_LIMITER)
register_rate_limit = _rate_limited(REGISTER_LIMITER)
recovery_rate_limit = _rate_limited(RECOVERY_LIMITER)
