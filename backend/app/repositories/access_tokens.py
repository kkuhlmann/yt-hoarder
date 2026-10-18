"""Personal access tokens: the bearer credentials the MCP endpoint accepts."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from sqlalchemy import delete, func, or_, update
from sqlmodel import select

from database import db
from models import PersonalAccessToken, User, utc_now

# A recognisable prefix, so a leaked token is easy to spot and to reject before any lookup.
TOKEN_PREFIX = 'ytdlh_'  # noqa: S105 — a public prefix, not a credential
MAX_TOKENS_PER_USER = 20
# Long enough to identify a token in a list, far too short to be one.
DISPLAY_PREFIX_LENGTH = 12
# last_used_at is informational, so it is written at most this often per token
# rather than on every MCP call.
TOUCH_INTERVAL = timedelta(seconds=60)


class TokenStatus(str, Enum):
    ACTIVE = 'active'
    EXPIRED = 'expired'
    # Created before the user's last password change.
    INVALIDATED = 'invalidated'
    # The user is unapproved or must change their password.
    BLOCKED = 'blocked'


@dataclass(frozen=True)
class ResolvedToken:
    user: User
    token: PersonalAccessToken


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def token_status(token: PersonalAccessToken, user: User, now: datetime) -> TokenStatus:
    """Why a token does or does not authenticate right now.

    Shared by the MCP auth path and the token list, so the list can show the
    user exactly why a token stopped working. The password rule mirrors the
    cookie session's `iat` check: every password change path writes
    `password_changed_at`, so tokens die with the sessions without any of those
    paths having to know tokens exist.
    """
    if token.expires_at is not None and token.expires_at <= now:
        return TokenStatus.EXPIRED
    if user.password_changed_at is not None and token.created_at < user.password_changed_at:
        return TokenStatus.INVALIDATED
    if not user.is_approved or user.must_change_password:
        return TokenStatus.BLOCKED
    return TokenStatus.ACTIVE


async def create_token(
    user_id: int, name: str, expires_in_days: int | None
) -> tuple[PersonalAccessToken, str]:
    """Create a token and return it with its plaintext, which is never stored."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = utc_now()
    token = PersonalAccessToken(
        user_id=user_id,
        name=name,
        token_hash=hash_token(raw),
        token_prefix=raw[:DISPLAY_PREFIX_LENGTH],
        created_at=now,
        expires_at=now + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    async with db.get_async_session() as session:
        session.add(token)
        await session.commit()
        await session.refresh(token)
    return token, raw


async def list_tokens_for_user(user_id: int) -> list[PersonalAccessToken]:
    async with db.get_async_session() as session:
        stmt = (
            select(PersonalAccessToken)
            .where(PersonalAccessToken.user_id == user_id)
            .order_by(PersonalAccessToken.created_at.desc(), PersonalAccessToken.id.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def count_tokens_for_user(user_id: int) -> int:
    async with db.get_async_session() as session:
        stmt = (
            select(func.count())
            .select_from(PersonalAccessToken)
            .where(PersonalAccessToken.user_id == user_id)
        )
        result = await session.execute(stmt)
        return result.scalar_one()


async def count_tokens_by_user() -> dict[int, int]:
    async with db.get_async_session() as session:
        stmt = select(PersonalAccessToken.user_id, func.count()).group_by(
            PersonalAccessToken.user_id
        )
        result = await session.execute(stmt)
        return dict(result.tuples().all())


async def delete_token(user_id: int, token_id: int) -> bool:
    """Revoke one of the user's own tokens. False when it is not theirs or is gone."""
    async with db.get_async_session() as session:
        stmt = delete(PersonalAccessToken).where(
            PersonalAccessToken.id == token_id, PersonalAccessToken.user_id == user_id
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount > 0


async def delete_all_tokens_for_user(user_id: int) -> int:
    async with db.get_async_session() as session:
        stmt = delete(PersonalAccessToken).where(PersonalAccessToken.user_id == user_id)
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount


async def resolve_token(raw: str) -> ResolvedToken | None:
    """The active token and its user for a presented plaintext, or None.

    Looked up by hash, so the database never sees the plaintext and the lookup
    leaks nothing useful through timing.
    """
    now = utc_now()
    async with db.get_async_session() as session:
        stmt = (
            select(PersonalAccessToken, User)
            .join(User, User.id == PersonalAccessToken.user_id)
            .where(PersonalAccessToken.token_hash == hash_token(raw))
        )
        row = (await session.execute(stmt)).first()
        if row is None:
            return None
        token, user = row
        if token_status(token, user, now) is not TokenStatus.ACTIVE:
            return None

        if token.last_used_at is None or token.last_used_at < now - TOUCH_INTERVAL:
            # Guarded so concurrent calls write once, without a read-modify-write race.
            await session.execute(
                update(PersonalAccessToken)
                .where(
                    PersonalAccessToken.id == token.id,
                    or_(
                        PersonalAccessToken.last_used_at.is_(None),
                        PersonalAccessToken.last_used_at < now - TOUCH_INTERVAL,
                    ),
                )
                .values(last_used_at=now)
            )
            await session.commit()
        return ResolvedToken(user=user, token=token)
