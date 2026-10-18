"""Personal access tokens: storage, the validity rules, and last-used bookkeeping."""

from datetime import timedelta

from sqlmodel import select

from database import db
from models import PersonalAccessToken, User, utc_now
from repositories import access_tokens as token_repo
from repositories import users as user_repo
from repositories.access_tokens import TokenStatus, hash_token, token_status


async def _user(username='alice', **fields) -> User:
    return await user_repo.create_user(
        User(username=username, password_hash='x', is_approved=True, **fields)
    )


async def _stored(token_id: int) -> PersonalAccessToken:
    async with db.get_async_session() as session:
        result = await session.execute(
            select(PersonalAccessToken).where(PersonalAccessToken.id == token_id)
        )
        return result.scalar_one()


async def test_only_a_hash_of_the_token_is_stored(clean_database):
    user = await _user()

    token, raw = await token_repo.create_token(user.id, 'laptop', None)

    stored = await _stored(token.id)
    assert raw.startswith('ytdlh_')
    assert stored.token_hash == hash_token(raw)
    assert raw not in (stored.token_hash, stored.token_prefix)
    assert raw.startswith(stored.token_prefix)


async def test_resolve_returns_the_user_for_an_active_token(clean_database):
    user = await _user()
    _token, raw = await token_repo.create_token(user.id, 'laptop', None)

    resolved = await token_repo.resolve_token(raw)

    assert resolved is not None
    assert resolved.user.id == user.id


async def test_resolve_rejects_an_unknown_token(clean_database):
    assert await token_repo.resolve_token('ytdlh_not-a-real-token') is None


async def test_expiry_is_honoured(clean_database):
    user = await _user()
    _token, raw = await token_repo.create_token(user.id, 'short', 30)

    assert await token_repo.resolve_token(raw) is not None
    async with db.get_async_session() as session:
        stored = (await session.execute(select(PersonalAccessToken))).scalar_one()
        stored.expires_at = utc_now() - timedelta(seconds=1)
        await session.commit()

    assert await token_repo.resolve_token(raw) is None


async def test_a_password_change_after_creation_invalidates_the_token(clean_database):
    user = await _user()
    _token, raw = await token_repo.create_token(user.id, 'laptop', None)

    await user_repo.update_user(user.id, password_changed_at=utc_now())

    assert await token_repo.resolve_token(raw) is None


async def test_a_token_created_after_a_password_change_works(clean_database):
    user = await _user(password_changed_at=utc_now() - timedelta(minutes=5))
    _token, raw = await token_repo.create_token(user.id, 'laptop', None)

    assert await token_repo.resolve_token(raw) is not None


async def test_unapproved_or_must_change_users_cannot_use_tokens(clean_database):
    user = await _user()
    _token, raw = await token_repo.create_token(user.id, 'laptop', None)

    await user_repo.update_user(user.id, must_change_password=True)
    assert await token_repo.resolve_token(raw) is None

    await user_repo.update_user(user.id, must_change_password=False, is_approved=False)
    assert await token_repo.resolve_token(raw) is None


def test_token_status_names_each_reason():
    now = utc_now()
    user = User(username='u', password_hash='x', is_approved=True)
    token = PersonalAccessToken(
        user_id=1, name='t', token_hash='h', token_prefix='p', created_at=now
    )

    assert token_status(token, user, now) is TokenStatus.ACTIVE
    assert (
        token_status(token.model_copy(update={'expires_at': now}), user, now) is TokenStatus.EXPIRED
    )
    changed = user.model_copy(update={'password_changed_at': now + timedelta(seconds=1)})
    assert token_status(token, changed, now) is TokenStatus.INVALIDATED
    blocked = user.model_copy(update={'is_approved': False})
    assert token_status(token, blocked, now) is TokenStatus.BLOCKED


async def test_last_used_is_written_at_most_once_a_minute(clean_database):
    user = await _user()
    token, raw = await token_repo.create_token(user.id, 'laptop', None)

    await token_repo.resolve_token(raw)
    first = (await _stored(token.id)).last_used_at
    await token_repo.resolve_token(raw)
    second = (await _stored(token.id)).last_used_at

    assert first is not None
    assert second == first

    async with db.get_async_session() as session:
        stored = (await session.execute(select(PersonalAccessToken))).scalar_one()
        stored.last_used_at = utc_now() - timedelta(minutes=2)
        await session.commit()
    await token_repo.resolve_token(raw)

    # A stale value is refreshed to now, not left two minutes old.
    assert (await _stored(token.id)).last_used_at > utc_now() - timedelta(minutes=1)


async def test_delete_only_removes_the_owners_token(clean_database):
    alice = await _user('alice')
    bob = await _user('bob')
    token, _raw = await token_repo.create_token(alice.id, 'laptop', None)

    assert await token_repo.delete_token(bob.id, token.id) is False
    assert await token_repo.delete_token(alice.id, token.id) is True
    assert await token_repo.list_tokens_for_user(alice.id) == []


async def test_counts_and_revoke_all(clean_database):
    alice = await _user('alice')
    bob = await _user('bob')
    for name in ('a', 'b'):
        await token_repo.create_token(alice.id, name, None)
    await token_repo.create_token(bob.id, 'c', None)

    assert await token_repo.count_tokens_for_user(alice.id) == 2
    assert await token_repo.count_tokens_by_user() == {alice.id: 2, bob.id: 1}
    assert await token_repo.delete_all_tokens_for_user(alice.id) == 2
    assert await token_repo.count_tokens_by_user() == {bob.id: 1}


async def test_tokens_go_with_their_user(clean_database):
    user = await _user()
    _token, raw = await token_repo.create_token(user.id, 'laptop', None)

    await user_repo.delete_user(user.id)

    assert await token_repo.resolve_token(raw) is None
    assert await token_repo.count_tokens_by_user() == {}
