"""The /mcp endpoint over real HTTP: token auth, rate limits, and per-user scoping.

These go through `main.app` so the route registration, the cookie auth middleware and
the SDK's stateless transport are all in the path. The per-user scoping cases are the
point: tools read the caller from a contextvar the endpoint sets, which only reaches them
because the stateless transport starts a fresh server task per request.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import update

import main
from auth import create_jwt_token
from config import settings
from database import db
from mcp_server.server import build_mcp_server
from models import PersonalAccessToken, User, utc_now
from rate_limit import MCP_AUTH_FAIL_LIMITER, MCP_TOKEN_LIMITER
from repositories import access_tokens as token_repo
from services import transcript as transcript_service
from tests.transcript_seed import StubEmbedder, seed_media, seed_user

ACCEPT = {'Accept': 'application/json, text/event-stream'}


@pytest.fixture(autouse=True)
def _clear_search_cache():
    transcript_service.semantic_cache.clear()
    yield
    transcript_service.semantic_cache.clear()


@pytest.fixture
def mcp_server(monkeypatch):
    monkeypatch.setattr(settings.mcp, 'enabled', True)
    server = build_mcp_server(embedder_factory=StubEmbedder)
    monkeypatch.setattr(main.mcp_endpoint, 'server', server)
    return server


@asynccontextmanager
async def serving(server):
    """Run the session manager and yield a client for the app.

    Inside the test rather than in an async fixture: the manager's anyio task group
    must be exited by the task that entered it, and pytest-asyncio tears fixtures down
    in a different task.
    """
    async with server.session_manager.run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://nas:8000') as client:
            yield client


@pytest.fixture
def two_users(clean_database, pgvector_schema):
    session = db.get_sync_session()
    try:
        alice = seed_user(session, 'alice')
        bob = seed_user(session, 'bob')
        alice_media = seed_media(
            session,
            owner_id=alice,
            url='https://www.youtube.com/watch?v=a',
            channel='Alice Channel',
            title='Alice Video',
            released=datetime(2025, 1, 1, tzinfo=UTC),
            blocks=['spacecraft notes from alice'],
            grant_access_to=alice,
        )
        bob_media = seed_media(
            session,
            owner_id=bob,
            url='https://www.youtube.com/watch?v=b',
            channel='Bob Channel',
            title='Bob Video',
            released=datetime(2025, 1, 1, tzinfo=UTC),
            blocks=['spacecraft notes from bob'],
            grant_access_to=bob,
        )
        return alice, bob, alice_media, bob_media
    finally:
        session.close()


async def _token(user_id: int, **kwargs) -> str:
    _row, raw = await token_repo.create_token(user_id, kwargs.pop('name', 'agent'), None)
    return raw


def _rpc(method: str, params: dict | None = None) -> dict:
    return {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}


async def _post(client, token: str | None, body: dict, path='/mcp', **headers):
    if token is not None:
        headers['Authorization'] = f'Bearer {token}'
    return await client.post(path, json=body, headers={**ACCEPT, **headers})


async def _search_media_ids(client, token: str) -> set[int]:
    response = await _post(
        client,
        token,
        _rpc('tools/call', {'name': 'search_transcripts', 'arguments': {'query': 'spacecraft'}}),
    )
    assert response.status_code == 200, response.text
    results = response.json()['result']['structuredContent']['results']
    return {hit['media_id'] for hit in results}


# --- availability ---


@pytest.mark.parametrize('path', ['/mcp', '/mcp/'])
async def test_disabled_endpoint_is_indistinguishable_from_unknown(monkeypatch, path):
    monkeypatch.setattr(settings.mcp, 'enabled', False)

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url='http://nas:8000') as client:
        response = await _post(client, 'ytdlh_whatever', _rpc('tools/list'), path=path)

    assert response.status_code == 404


@pytest.mark.parametrize('path', ['/mcp', '/mcp/'])
async def test_both_spellings_of_the_path_work(mcp_server, two_users, path):
    async with serving(mcp_server) as client:
        alice, *_ = two_users
        token = await _token(alice)

        response = await _post(client, token, _rpc('tools/list'), path=path)

        assert response.status_code == 200
        assert {t['name'] for t in response.json()['result']['tools']} == {
            'library_overview',
            'search_transcripts',
            'get_transcript',
        }


def test_server_runs_stateless(mcp_server):
    assert mcp_server.session_manager.stateless is True


async def test_any_host_is_answered(mcp_server, two_users):
    async with serving(mcp_server) as client:
        alice, *_ = two_users
        token = await _token(alice)

        response = await _post(client, token, _rpc('tools/list'), Host='hoarder.lan:1234')

        # The SDK's default Host allowlist is localhost-only and would answer 421.
        assert response.status_code == 200


# --- authentication ---


@pytest.mark.parametrize('token', [None, 'ytdlh_not-a-real-token', 'not-even-prefixed'])
async def test_requests_without_a_valid_token_are_refused(mcp_server, two_users, token):
    async with serving(mcp_server) as client:
        response = await _post(client, token, _rpc('tools/list'))

        assert response.status_code == 401
        challenge = response.headers['WWW-Authenticate']
        assert challenge.startswith('Bearer')
        # No RFC 9728 pointer: there is no OAuth server for a client to go looking for.
        assert 'resource_metadata' not in challenge


async def test_a_session_cookie_does_not_authenticate_mcp(mcp_server, two_users):
    async with serving(mcp_server) as client:
        alice, *_ = two_users
        cookie = create_jwt_token(alice, 'alice', False)

        response = await _post(client, None, _rpc('tools/list'), Cookie=f'auth_token={cookie}')

        assert response.status_code == 401


async def test_an_expired_token_is_refused(mcp_server, two_users):
    async with serving(mcp_server) as client:
        alice, *_ = two_users
        token = await _token(alice)
        async with db.get_async_session() as session:
            await session.execute(
                update(PersonalAccessToken).values(expires_at=utc_now() - timedelta(seconds=1))
            )
            await session.commit()

        assert (await _post(client, token, _rpc('tools/list'))).status_code == 401


async def test_a_revoked_token_is_refused(mcp_server, two_users):
    async with serving(mcp_server) as client:
        alice, *_ = two_users
        token = await _token(alice)
        await token_repo.delete_all_tokens_for_user(alice)

        assert (await _post(client, token, _rpc('tools/list'))).status_code == 401


async def test_a_password_change_kills_existing_tokens(mcp_server, two_users):
    async with serving(mcp_server) as client:
        alice, *_ = two_users
        token = await _token(alice)
        assert (await _post(client, token, _rpc('tools/list'))).status_code == 200

        async with db.get_async_session() as session:
            await session.execute(
                update(User).where(User.id == alice).values(password_changed_at=utc_now())
            )
            await session.commit()

        assert (await _post(client, token, _rpc('tools/list'))).status_code == 401


# --- rate limits ---


async def test_a_token_is_rate_limited(mcp_server, two_users, monkeypatch):
    async with serving(mcp_server) as client:
        alice, *_ = two_users
        token = await _token(alice)
        monkeypatch.setattr(MCP_TOKEN_LIMITER, 'limit', 2)

        for _ in range(2):
            assert (await _post(client, token, _rpc('tools/list'))).status_code == 200
        limited = await _post(client, token, _rpc('tools/list'))

        assert limited.status_code == 429
        assert int(limited.headers['Retry-After']) >= 1


async def test_repeated_failures_lock_out_the_address(mcp_server, two_users, monkeypatch):
    async with serving(mcp_server) as client:
        alice, *_ = two_users
        token = await _token(alice)
        monkeypatch.setattr(MCP_AUTH_FAIL_LIMITER, 'limit', 2)

        for _ in range(2):
            assert (await _post(client, 'ytdlh_wrong', _rpc('tools/list'))).status_code == 401

        # Checked before the token is even looked up.
        assert (await _post(client, token, _rpc('tools/list'))).status_code == 429


async def test_successful_calls_do_not_count_as_failures(mcp_server, two_users, monkeypatch):
    async with serving(mcp_server) as client:
        alice, *_ = two_users
        token = await _token(alice)
        monkeypatch.setattr(MCP_AUTH_FAIL_LIMITER, 'limit', 2)

        for _ in range(4):
            assert (await _post(client, token, _rpc('tools/list'))).status_code == 200


# --- scoping ---


async def test_each_token_sees_only_its_users_library(mcp_server, two_users):
    async with serving(mcp_server) as client:
        alice, bob, alice_media, bob_media = two_users
        alice_token = await _token(alice)
        bob_token = await _token(bob)

        assert await _search_media_ids(client, alice_token) == {alice_media}
        assert await _search_media_ids(client, bob_token) == {bob_media}


async def test_concurrent_requests_never_cross_users(mcp_server, two_users):
    async with serving(mcp_server) as client:
        alice, bob, alice_media, bob_media = two_users
        alice_token = await _token(alice)
        bob_token = await _token(bob)
        tokens = [alice_token, bob_token] * 6

        results = await asyncio.gather(*(_search_media_ids(client, t) for t in tokens))

        for token, media_ids in zip(tokens, results, strict=True):
            assert media_ids == ({alice_media} if token == alice_token else {bob_media})


async def test_app_links_follow_the_address_the_agent_used(mcp_server, two_users):
    async with serving(mcp_server) as client:
        alice, _bob, alice_media, _ = two_users
        token = await _token(alice)

        response = await _post(
            client,
            token,
            _rpc(
                'tools/call', {'name': 'search_transcripts', 'arguments': {'query': 'spacecraft'}}
            ),
        )

        hit = response.json()['result']['structuredContent']['results'][0]
        # Tests run as the dev layout, where the UI is on :3000 rather than the API's :8000.
        assert hit['app_url'] == f'http://nas:3000/?media={alice_media}&t=0'


async def test_public_url_overrides_the_request_address(mcp_server, two_users, monkeypatch):
    async with serving(mcp_server) as client:
        alice, _bob, alice_media, _ = two_users
        token = await _token(alice)
        monkeypatch.setattr(settings.mcp, 'public_url', 'https://hoarder.example.com')

        response = await _post(
            client,
            token,
            _rpc(
                'tools/call', {'name': 'search_transcripts', 'arguments': {'query': 'spacecraft'}}
            ),
        )

        hit = response.json()['result']['structuredContent']['results'][0]
        assert hit['app_url'] == f'https://hoarder.example.com/?media={alice_media}&t=0'


# --- a real client ---


async def test_the_sdk_client_negotiates_and_calls_tools(mcp_server, two_users):
    """What an agent actually does: the SDK client's own handshake, then a tool call.

    The raw JSON-RPC cases above take the SDK's legacy path; this one lets the client
    negotiate, so it covers whichever protocol path real clients land on.
    """
    alice, _bob, alice_media, _ = two_users
    token = await _token(alice)

    async with serving(mcp_server):
        http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=main.app),
            headers={'Authorization': f'Bearer {token}'},
        )
        transport = streamable_http_client('http://nas:8000/mcp', http_client=http)
        async with http, Client(transport) as client:
            tools = (await client.list_tools()).tools
            result = await client.call_tool('search_transcripts', {'query': 'spacecraft'})
            negotiated = client.protocol_version

    assert {t.name for t in tools} == {'library_overview', 'search_transcripts', 'get_transcript'}
    assert {hit['media_id'] for hit in result.structured_content['results']} == {alice_media}
    assert negotiated
