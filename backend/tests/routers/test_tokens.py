"""Managing MCP access tokens over REST, with the normal cookie session."""

import pytest
from fastapi.testclient import TestClient

from config import settings
from main import app
from repositories.access_tokens import MAX_TOKENS_PER_USER


@pytest.fixture
def mcp_on(monkeypatch):
    monkeypatch.setattr(settings.mcp, 'enabled', True)


def _second_user(client) -> tuple[int, TestClient]:
    reg = client.post('/auth/register', json={'username': 'user2', 'password': 'pass1234'})
    user2_id = reg.json()['id']
    client.post(f'/auth/users/{user2_id}/approve')
    client2 = TestClient(app)
    client2.post('/auth/login', json={'username': 'user2', 'password': 'pass1234'})
    return user2_id, client2


def test_create_returns_the_token_once_and_list_never_does(authenticated_client, mcp_on):
    created = authenticated_client.post('/auth/tokens', json={'name': '  laptop  '})

    assert created.status_code == 201
    body = created.json()
    assert body['token'].startswith('ytdlh_')
    assert body['name'] == 'laptop'
    assert body['status'] == 'active'
    assert body['expires_at'] is None

    listed = authenticated_client.get('/auth/tokens').json()
    assert [t['id'] for t in listed] == [body['id']]
    assert 'token' not in listed[0]
    assert body['token'].startswith(listed[0]['token_prefix'])


def test_create_404s_while_mcp_is_disabled(authenticated_client, monkeypatch):
    monkeypatch.setattr(settings.mcp, 'enabled', False)

    assert authenticated_client.post('/auth/tokens', json={'name': 'x'}).status_code == 404


@pytest.mark.parametrize(
    'payload',
    [{'name': '   '}, {'name': 'x' * 65}, {'name': 'ok', 'expires_in_days': 0}],
)
def test_create_validates_name_and_expiry(authenticated_client, mcp_on, payload):
    assert authenticated_client.post('/auth/tokens', json=payload).status_code == 422


def test_expiry_is_set_from_days(authenticated_client, mcp_on):
    body = authenticated_client.post(
        '/auth/tokens', json={'name': 'short', 'expires_in_days': 30}
    ).json()

    assert body['expires_at'] is not None


def test_create_stops_at_the_cap(authenticated_client, mcp_on):
    for index in range(MAX_TOKENS_PER_USER):
        assert (
            authenticated_client.post('/auth/tokens', json={'name': f't{index}'}).status_code == 201
        )

    assert authenticated_client.post('/auth/tokens', json={'name': 'one more'}).status_code == 409


def test_users_cannot_see_or_revoke_each_others_tokens(authenticated_client, mcp_on):
    _user2_id, client2 = _second_user(authenticated_client)
    mine = authenticated_client.post('/auth/tokens', json={'name': 'admin'}).json()

    assert client2.get('/auth/tokens').json() == []
    assert client2.delete(f'/auth/tokens/{mine["id"]}').status_code == 404
    assert authenticated_client.delete(f'/auth/tokens/{mine["id"]}').status_code == 204
    assert authenticated_client.get('/auth/tokens').json() == []


def test_admin_can_revoke_all_of_a_users_tokens(authenticated_client, mcp_on):
    user2_id, client2 = _second_user(authenticated_client)
    client2.post('/auth/tokens', json={'name': 'a'})
    client2.post('/auth/tokens', json={'name': 'b'})

    users = authenticated_client.get('/auth/users').json()
    assert next(u for u in users if u['id'] == user2_id)['token_count'] == 2

    revoked = authenticated_client.delete(f'/auth/tokens/users/{user2_id}')

    assert revoked.json() == {'revoked': 2}
    assert client2.get('/auth/tokens').json() == []


def test_only_admins_revoke_other_users_tokens(authenticated_client, mcp_on):
    _user2_id, client2 = _second_user(authenticated_client)
    admin_id = next(
        u['id'] for u in authenticated_client.get('/auth/users').json() if u['is_admin']
    )

    assert client2.delete(f'/auth/tokens/users/{admin_id}').status_code == 403


def test_token_routes_require_a_session(test_database):
    assert TestClient(app).get('/auth/tokens').status_code == 401


def test_a_bearer_token_does_not_authenticate_rest(authenticated_client, mcp_on):
    raw = authenticated_client.post('/auth/tokens', json={'name': 'agent'}).json()['token']

    anonymous = TestClient(app)
    response = anonymous.get('/auth/tokens', headers={'Authorization': f'Bearer {raw}'})

    assert response.status_code == 401


def test_me_reports_whether_mcp_is_enabled(authenticated_client, monkeypatch):
    monkeypatch.setattr(settings.mcp, 'enabled', True)
    assert authenticated_client.get('/auth/me').json()['mcp_enabled'] is True

    monkeypatch.setattr(settings.mcp, 'enabled', False)
    assert authenticated_client.get('/auth/me').json()['mcp_enabled'] is False
