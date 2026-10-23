import pytest

from database import db
from models import JobType, MediaType, Subscription

DUP_URL = 'https://www.youtube.com/@DupChannel'


def _insert_subscription(user_id, url=DUP_URL, string_match=None, audio_only=False):
    """Insert a subscription row directly (the router's success path only enqueues a task)."""
    session = db.get_sync_session()
    try:
        sub = Subscription(
            url=url,
            channel='Dup Channel',
            audio_only=audio_only,
            media_type=MediaType.AUDIO if audio_only else MediaType.VIDEO,
            string_match=string_match,
            job_type=JobType.CHANNEL_SUBSCRIPTION,
            user_id=user_id,
        )
        session.add(sub)
        session.commit()
        session.refresh(sub)
        return sub.id
    finally:
        session.close()


def _get_admin_id(client):
    users = client.get('/auth/users').json()
    return next(u for u in users if u['username'] == 'testadmin')['id']


def _dup_payload(string_match='News'):
    return {
        'url': DUP_URL,
        'audio_only': False,
        'media_type': 'VIDEO',
        'string_match': string_match,
        'job_type': 'CHANNEL_SUBSCRIPTION',
    }


def test_get_one_subscription(authenticated_client):
    response = authenticated_client.get('/subscriptions/1')
    assert response.status_code == 200
    data = response.json()
    assert data['id'] == 1
    assert data['url'] == 'https://www.youtube.com/@RickAstleyYT'
    assert data['channel'] == 'Lesh'


def test_get_one_subscription_not_exist(authenticated_client):
    response = authenticated_client.get('/subscriptions/999')
    assert response.status_code == 404
    assert 'not found' in response.json()['detail'].lower()


def test_get_all_subscriptions(authenticated_client):
    response = authenticated_client.get('/subscriptions?page=1&page_size=25')
    assert response.status_code == 200
    data = response.json()
    assert 'records' in data
    assert 'count_records' in data
    assert len(data['records']) == 2

    response = authenticated_client.get('/subscriptions?page=1&page_size=1')
    assert response.status_code == 200
    data = response.json()
    assert len(data['records']) == 1
    sub_1 = data['records'][0]

    response = authenticated_client.get('/subscriptions?page=2&page_size=1')
    assert response.status_code == 200
    sub_2 = response.json()['records'][0]

    assert sub_1['id'] != sub_2['id']


def test_update_subscription_enabled(authenticated_client):
    """The row toggle sends a partial body carrying only `enabled`."""
    response = authenticated_client.put('/subscriptions/1', json={'enabled': False})
    assert response.status_code == 201
    assert response.json()['enabled'] is False

    assert authenticated_client.get('/subscriptions/1').json()['enabled'] is False

    response = authenticated_client.put('/subscriptions/1', json={'enabled': True})
    assert response.status_code == 201
    assert response.json()['enabled'] is True


def test_delete_subscription(authenticated_client):
    response = authenticated_client.delete('/subscriptions/1')
    assert response.status_code == 204


def test_delete_subscription_not_exist(authenticated_client):
    response = authenticated_client.delete('/subscriptions/999')
    assert response.status_code == 404
    assert 'not found' in response.json()['detail'].lower()


async def test_add_subscription(monkeypatch, authenticated_client):
    from unittest.mock import AsyncMock

    monkeypatch.setattr('routers.subscriptions.orch.submit', AsyncMock(return_value='TASK_ID'))

    # testing channel url
    response = authenticated_client.post(
        '/subscriptions',
        json={
            'url': 'https://www.youtube.com/@RickAstleyYT',
            'channel': None,
            'audio_only': True,
            'media_type': 'AUDIO',
            'string_match': 'Test Match',
            'overwrite': False,
            'date_filter': '2023-07-27T00:00:00',
            'job_type': 'CHANNEL_SUBSCRIPTION',
            'generate_transcript': False,
        },
    )
    assert response.status_code == 201
    assert response.json()['task'] == 'TASK_ID'


def test_add_subscription_duplicate_same_user(authenticated_client):
    """Same user submitting identical details gets DUPLICATE_SUBSCRIPTION."""
    admin_id = _get_admin_id(authenticated_client)
    _insert_subscription(admin_id, string_match='News')

    response = authenticated_client.post('/subscriptions', json=_dup_payload())
    assert response.status_code == 201
    assert response.json()['task'] == 'DUPLICATE_SUBSCRIPTION'


def test_add_subscription_duplicate_case_insensitive_string_match(authenticated_client):
    """string_match dedup stays case-insensitive within the per-user scope."""
    admin_id = _get_admin_id(authenticated_client)
    _insert_subscription(admin_id, string_match='News')

    response = authenticated_client.post('/subscriptions', json=_dup_payload(string_match='nEwS'))
    assert response.status_code == 201
    assert response.json()['task'] == 'DUPLICATE_SUBSCRIPTION'


def test_add_subscription_same_details_different_user_allowed(monkeypatch, authenticated_client):
    """A different user may hold an identical subscription (per-user dedup)."""
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from main import app

    monkeypatch.setattr('routers.subscriptions.orch.submit', AsyncMock(return_value='TASK_ID_2'))

    admin_id = _get_admin_id(authenticated_client)
    _insert_subscription(admin_id, string_match='News')

    reg = authenticated_client.post(
        '/auth/register', json={'username': 'user2', 'password': 'pass123'}
    )
    user2_id = reg.json()['id']
    authenticated_client.post(f'/auth/users/{user2_id}/approve')
    client2 = TestClient(app)
    client2.post('/auth/login', json={'username': 'user2', 'password': 'pass123'})

    response = client2.post('/subscriptions', json=_dup_payload())
    assert response.status_code == 201
    assert response.json()['task'] == 'TASK_ID_2'


def test_unauthenticated_rejected(test_database):
    """Unauthenticated requests to protected endpoints return 401."""
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)
    response = client.get('/subscriptions')
    assert response.status_code == 401


@pytest.fixture
def check_specs(monkeypatch):
    """Every JobSpec POST /subscriptions/{id}/check hands the orchestrator."""
    specs = []

    async def record(spec):
        specs.append(spec)
        return spec.task_id

    monkeypatch.setattr('routers.subscriptions.orch.submit', record)
    return specs


def _second_user_client(admin_client, username='user2'):
    from fastapi.testclient import TestClient

    from main import app

    user_id = admin_client.post(
        '/auth/register', json={'username': username, 'password': 'pass123'}
    ).json()['id']
    admin_client.post(f'/auth/users/{user_id}/approve')
    client = TestClient(app)
    client.post('/auth/login', json={'username': username, 'password': 'pass123'})
    return user_id, client


def test_check_subscription_submits_a_deduped_priority_zero_check(
    authenticated_client, check_specs
):
    from orchestrator import SUBSCRIPTION_CHECK_JOB

    response = authenticated_client.post('/subscriptions/1/check')

    assert response.status_code == 202
    assert response.json() == {'task': 'subscription-check-1'}
    (spec,) = check_specs
    assert spec.job_name == SUBSCRIPTION_CHECK_JOB
    assert spec.args == (1,)
    assert spec.task_id == 'subscription-check-1'
    assert spec.priority == 0
    assert spec.tracked is False
    assert spec.user_id == _get_admin_id(authenticated_client)


def test_check_subscription_runs_on_a_disabled_subscription(authenticated_client, check_specs):
    authenticated_client.put('/subscriptions/1', json={'enabled': False})

    response = authenticated_client.post('/subscriptions/1/check')

    assert response.status_code == 202
    assert len(check_specs) == 1


def test_check_subscription_is_attributed_to_the_owner_not_the_caller(
    authenticated_client, check_specs
):
    """An admin's check must stay pollable by the owner, who shares its task_id."""
    user2_id, _ = _second_user_client(authenticated_client)
    sub_id = _insert_subscription(user2_id)

    response = authenticated_client.post(f'/subscriptions/{sub_id}/check')

    assert response.status_code == 202
    assert check_specs[0].user_id == user2_id


def test_check_subscription_refuses_a_shared_user(authenticated_client, check_specs):
    user2_id, client2 = _second_user_client(authenticated_client)
    authenticated_client.post('/subscriptions/1/share', json={'user_id': user2_id})
    assert client2.get('/subscriptions/1').status_code == 200

    response = client2.post('/subscriptions/1/check')

    assert response.status_code == 404
    assert check_specs == []


def test_check_subscription_not_exist(authenticated_client, check_specs):
    response = authenticated_client.post('/subscriptions/999/check')

    assert response.status_code == 404
    assert check_specs == []


def test_check_subscription_unauthenticated(test_database, check_specs):
    from fastapi.testclient import TestClient

    from main import app

    response = TestClient(app).post('/subscriptions/1/check')

    assert response.status_code == 401
    assert check_specs == []
