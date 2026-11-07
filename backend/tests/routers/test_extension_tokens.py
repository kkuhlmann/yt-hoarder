"""Extension tokens over REST: the add-only routes they open, and nothing beyond them."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from config import settings
from database import db
from main import app
from models import (
    MediaType,
    PersonalAccessToken,
    Subscription,
    TaskRecord,
    TaskStatus,
    TaskType,
    User,
    utc_now,
)
from rate_limit import (
    EXTENSION_AUTH_FAIL_LIMITER,
    EXTENSION_POLL_LIMITER,
    EXTENSION_TOKEN_LIMITER,
)
from routers.extension import ExtensionTask
from routers.ytdl_router import CANCELLED_RETRY_MESSAGE, get_app_version

VIDEO_URL = 'https://www.youtube.com/watch?v=ExTeNsIoN01'
CHANNEL_URL = 'https://www.youtube.com/@ExtensionChannel'


@pytest.fixture
def submitted(monkeypatch):
    """The JobSpecs POST /ytdl/ and POST /subscriptions hand the orchestrator."""
    specs = []

    async def record(spec):
        specs.append(spec)
        return 'job-id'

    monkeypatch.setattr('routers.ytdl_router.orch.submit', record)
    monkeypatch.setattr('routers.subscriptions.orch.submit', record)
    return specs


@pytest.fixture
def no_seeded_tasks(authenticated_client):
    """authenticated_client's database arrives seeded with the admin's task records.

    Soft-deleted rather than removed: media_details rows still reference them.
    """
    _update(TaskRecord, deleted_at=utc_now())


def _create(client, scope='extension') -> str:
    response = client.post('/auth/tokens', json={'name': 'browser', 'scope': scope})
    assert response.status_code == 201, response.text
    return response.json()['token']


def _bearer(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _download(client, headers=None, url=VIDEO_URL, media_type='VIDEO'):
    return client.post('/ytdl/', json={'url': url, 'media_type': media_type}, headers=headers)


def _subscribe(client, headers=None, **fields):
    body = {'url': CHANNEL_URL, 'media_type': 'AUDIO', 'audio_only': True, **fields}
    return client.post('/subscriptions', json=body, headers=headers)


def _user_id(client) -> int:
    return client.get('/auth/me').json()['id']


def _second_user(client) -> tuple[int, TestClient]:
    reg = client.post('/auth/register', json={'username': 'user2', 'password': 'pass1234'})
    user2_id = reg.json()['id']
    client.post(f'/auth/users/{user2_id}/approve')
    client2 = TestClient(app)
    client2.post('/auth/login', json={'username': 'user2', 'password': 'pass1234'})
    return user2_id, client2


def _update(model, **values):
    session = db.get_sync_session()
    try:
        session.execute(update(model).values(**values))
        session.commit()
    finally:
        session.close()


def _add(row):
    session = db.get_sync_session()
    try:
        session.add(row)
        session.commit()
    finally:
        session.close()


# --- what an extension token opens ---


def test_an_extension_token_adds_a_download(authenticated_client, submitted):
    token = _create(authenticated_client)

    response = _download(TestClient(app), _bearer(token))

    assert response.status_code == 201
    assert response.json()['task_id']
    assert [spec.user_id for spec in submitted] == [_user_id(authenticated_client)]


def test_an_extension_token_adds_a_subscription(authenticated_client, submitted):
    token = _create(authenticated_client)

    response = _subscribe(TestClient(app), _bearer(token))

    assert response.status_code == 201
    assert response.json() == {'task': 'job-id'}
    assert [spec.user_id for spec in submitted] == [_user_id(authenticated_client)]


def test_an_extension_token_can_overwrite_a_single_download(authenticated_client, submitted):
    token = _create(authenticated_client)

    response = TestClient(app).post(
        '/ytdl/',
        json={'url': VIDEO_URL, 'media_type': 'VIDEO', 'overwrite': True},
        headers=_bearer(token),
    )

    assert response.status_code == 201
    assert len(submitted) == 1


def test_an_extension_token_cannot_add_an_overwriting_subscription(authenticated_client, submitted):
    token = _create(authenticated_client)

    response = _subscribe(TestClient(app), _bearer(token), overwrite=True)

    assert response.status_code == 403
    assert submitted == []


def test_the_cookie_can_still_add_an_overwriting_subscription(authenticated_client, submitted):
    response = _subscribe(authenticated_client, overwrite=True)

    assert response.status_code == 201
    assert len(submitted) == 1


def test_extension_me_names_the_tokens_user(authenticated_client):
    token = _create(authenticated_client)

    response = TestClient(app).get('/extension/me', headers=_bearer(token))

    assert response.status_code == 200
    body = response.json()
    assert body['username'] == 'testadmin'
    assert body['token_name'] == 'browser'
    assert token.startswith(body['token_prefix'])
    assert body['expires_at'] is None
    assert body['app_version'] == get_app_version()


def test_extension_tokens_work_while_mcp_is_disabled(authenticated_client, submitted, monkeypatch):
    monkeypatch.setattr(settings.mcp, 'enabled', False)
    token = _create(authenticated_client)

    assert _download(TestClient(app), _bearer(token)).status_code == 201


def _task(user_id, task_id, status=TaskStatus.IN_PROGRESS, **fields):
    _add(
        TaskRecord(
            task_id=task_id,
            task_type=TaskType.DOWNLOAD,
            status=status,
            user_id=user_id,
            **fields,
        )
    )


def test_extension_tasks_lists_the_tokens_users_progress(authenticated_client, no_seeded_tasks):
    token = _create(authenticated_client)
    _task(
        _user_id(authenticated_client),
        'running-task',
        title='A video',
        channel='A channel',
        media_type=MediaType.VIDEO,
        percent_complete=42,
        eta_seconds=90.5,
        download_phase='VIDEO',
        status_message='Downloading',
    )

    response = TestClient(app).get('/extension/tasks', headers=_bearer(token))

    assert response.status_code == 200
    body = response.json()
    assert body['has_more'] is False
    assert [task['task_id'] for task in body['tasks']] == ['running-task']
    task = body['tasks'][0]
    assert task['status'] == 'IN_PROGRESS'
    assert task['task_type'] == 'DOWNLOAD'
    assert task['title'] == 'A video'
    assert task['channel'] == 'A channel'
    assert task['percent_complete'] == 42
    assert task['eta_seconds'] == 90.5
    assert task['download_phase'] == 'VIDEO'


def test_extension_tasks_names_the_zone_of_every_timestamp(authenticated_client, no_seeded_tasks):
    token = _create(authenticated_client)
    _task(
        _user_id(authenticated_client),
        'sleeping-task',
        sleep_until=utc_now() + timedelta(minutes=2),
    )

    task = TestClient(app).get('/extension/tasks', headers=_bearer(token)).json()['tasks'][0]

    assert task['updated_at'].endswith('Z') or task['updated_at'].endswith('+00:00')
    assert task['sleep_until'].endswith('Z') or task['sleep_until'].endswith('+00:00')


def test_extension_tasks_exposes_only_the_documented_fields(authenticated_client, no_seeded_tasks):
    token = _create(authenticated_client)
    _task(
        _user_id(authenticated_client),
        'placeholder-task',
        status=TaskStatus.RESOLVING,
        download_job_url=VIDEO_URL,
        pending_payload={'url': VIDEO_URL, 'secret': 'payload'},
    )

    response = TestClient(app).get('/extension/tasks', headers=_bearer(token))

    task = response.json()['tasks'][0]
    assert set(task) == set(ExtensionTask.model_fields)
    assert 'payload' not in response.text
    assert 'user_id' not in task


def test_extension_tasks_never_shows_another_users_tasks(authenticated_client, no_seeded_tasks):
    user2_id, client2 = _second_user(authenticated_client)
    _task(user2_id, 'user2-task')
    _task(_user_id(authenticated_client), 'admin-task')
    admin_token = _create(authenticated_client)
    user2_token = _create(client2)

    admin_view = TestClient(app).get('/extension/tasks', headers=_bearer(admin_token)).json()
    user2_view = TestClient(app).get('/extension/tasks', headers=_bearer(user2_token)).json()

    # The admin's token is an ordinary user's, never the admin view.
    assert [t['task_id'] for t in admin_view['tasks']] == ['admin-task']
    assert [t['task_id'] for t in user2_view['tasks']] == ['user2-task']


def test_extension_tasks_leaves_out_finished_and_hidden_ones(authenticated_client, no_seeded_tasks):
    token = _create(authenticated_client)
    uid = _user_id(authenticated_client)
    _task(uid, 'running-task')
    _task(uid, 'just-finished', status=TaskStatus.COMPLETE)
    _task(
        uid,
        'finished-yesterday',
        status=TaskStatus.COMPLETE,
        updated_at=utc_now() - timedelta(days=1),
    )
    _task(uid, 'cancelled-task', status=TaskStatus.CANCELLED)
    _task(uid, 'deleted-task', deleted_at=utc_now())

    response = TestClient(app).get('/extension/tasks', headers=_bearer(token))

    ids = {task['task_id'] for task in response.json()['tasks']}
    assert ids == {'running-task', 'just-finished'}


def test_extension_tasks_reports_when_the_list_was_cut_short(
    authenticated_client, no_seeded_tasks, monkeypatch
):
    monkeypatch.setattr('routers.extension.TASK_FEED_LIMIT', 2)
    token = _create(authenticated_client)
    for n in range(3):
        _task(_user_id(authenticated_client), f'task-{n}')

    body = TestClient(app).get('/extension/tasks', headers=_bearer(token)).json()

    assert len(body['tasks']) == 2
    assert body['has_more'] is True


# --- and what it doesn't ---


@pytest.mark.parametrize(
    ('method', 'path'),
    [
        ('GET', '/media-details'),
        ('GET', '/media-details/semantic/search?semantic_search=x'),
        ('GET', '/subscriptions'),
        ('DELETE', '/subscriptions/1'),
        ('POST', '/subscriptions/1/check'),
        ('GET', '/tasks'),
        ('GET', '/tasks/stats'),
        ('GET', '/tasks/some-task'),
        ('GET', '/sse/progress'),
        ('GET', '/auth/tokens'),
        ('DELETE', '/auth/tokens/1'),
        ('GET', '/auth/users'),
    ],
)
def test_an_extension_token_opens_nothing_else(authenticated_client, method, path):
    token = _create(authenticated_client)

    response = TestClient(app).request(method, path, headers=_bearer(token))

    assert response.status_code == 401


@pytest.mark.parametrize(
    'call',
    [
        _download,
        _subscribe,
        lambda client, headers: client.get('/extension/me', headers=headers),
        lambda client, headers: client.get('/extension/tasks', headers=headers),
    ],
)
def test_an_mcp_token_is_refused_on_the_extension_routes(
    authenticated_client, submitted, mcp_on, call
):
    token = _create(authenticated_client, scope='mcp')

    response = call(TestClient(app), _bearer(token))

    assert response.status_code == 401
    assert response.headers['WWW-Authenticate'].startswith('Bearer')
    assert submitted == []


# --- the cookie ---


def test_the_cookie_does_not_authenticate_extension_me(authenticated_client):
    assert authenticated_client.get('/extension/me').status_code == 401


def test_the_cookie_does_not_authenticate_extension_tasks(authenticated_client):
    assert authenticated_client.get('/extension/tasks').status_code == 401


def test_the_cookie_still_adds_downloads_and_subscriptions(authenticated_client, submitted):
    assert _download(authenticated_client).status_code == 201
    assert _subscribe(authenticated_client).status_code == 201


@pytest.mark.parametrize('scope', ['extension', 'mcp'])
def test_a_rejected_token_never_falls_back_to_the_cookie(
    authenticated_client, submitted, mcp_on, scope
):
    token = _create(authenticated_client, scope=scope)
    _update(PersonalAccessToken, expires_at=utc_now() - timedelta(seconds=1))

    # authenticated_client still carries its valid session cookie.
    assert _download(authenticated_client, _bearer(token)).status_code == 401
    assert _download(authenticated_client, _bearer('ytdlh_not-a-real-token')).status_code == 401
    assert submitted == []


def test_a_non_token_authorization_header_leaves_the_cookie_path_alone(
    authenticated_client, submitted
):
    # A proxy adding Basic auth in front of the app must not break the web UI.
    response = _download(authenticated_client, {'Authorization': 'Basic dXNlcjpwYXNz'})

    assert response.status_code == 201


# --- validity ---


@pytest.mark.parametrize(
    ('model', 'values'),
    [
        (PersonalAccessToken, {'expires_at': utc_now() - timedelta(seconds=1)}),
        # Evaluated at collection, so the margin must outlast the run up to this test.
        (User, {'password_changed_at': utc_now() + timedelta(days=1)}),
        (User, {'must_change_password': True}),
        (User, {'is_approved': False}),
    ],
)
def test_the_token_validity_rules_apply(authenticated_client, submitted, model, values):
    token = _create(authenticated_client)
    _update(model, **values)

    assert _download(TestClient(app), _bearer(token)).status_code == 401
    assert submitted == []


def test_a_password_change_invalidates_the_token(authenticated_client, submitted):
    token = _create(authenticated_client)

    changed = authenticated_client.post(
        '/auth/me/change-password',
        json={'current_password': 'testpass123', 'new_password': 'newpass456'},
    )

    assert changed.status_code == 200
    assert _download(TestClient(app), _bearer(token)).status_code == 401


def test_a_revoked_token_is_refused(authenticated_client):
    token = _create(authenticated_client)
    token_id = authenticated_client.get('/auth/tokens').json()[0]['id']

    authenticated_client.delete(f'/auth/tokens/{token_id}')

    assert TestClient(app).get('/extension/me', headers=_bearer(token)).status_code == 401


# --- per-user isolation ---


def test_a_token_acts_as_its_own_user_whoever_holds_the_cookie(authenticated_client, submitted):
    user2_id, client2 = _second_user(authenticated_client)
    token = _create(client2)

    # The admin's cookie rides along; the token decides who is asking.
    response = _download(authenticated_client, _bearer(token))

    assert response.status_code == 201
    assert [spec.user_id for spec in submitted] == [user2_id]
    me = authenticated_client.get('/extension/me', headers=_bearer(token)).json()
    assert me['username'] == 'user2'


def test_the_tokens_user_storage_limit_applies(authenticated_client, submitted):
    user2_id, client2 = _second_user(authenticated_client)
    token = _create(client2)
    session = db.get_sync_session()
    try:
        session.get(User, user2_id).storage_limit_bytes = 0
        session.commit()
    finally:
        session.close()

    response = _download(TestClient(app), _bearer(token))

    assert response.status_code == 403
    assert response.json()['detail']['message'] == 'Storage limit reached'


# --- the server's own messages come through unchanged ---


def test_the_cancelled_conflict_reaches_the_extension(authenticated_client, submitted):
    token = _create(authenticated_client)
    _add(
        TaskRecord(
            task_id='cancelled-download-task',
            task_type=TaskType.DOWNLOAD,
            status=TaskStatus.CANCELLED,
            download_job_url=VIDEO_URL,
            media_type=MediaType.VIDEO,
            user_id=_user_id(authenticated_client),
        )
    )

    response = _download(TestClient(app), _bearer(token))

    assert response.status_code == 409
    assert response.json()['detail']['message'] == CANCELLED_RETRY_MESSAGE


def test_a_duplicate_subscription_is_reported(authenticated_client, submitted):
    token = _create(authenticated_client)
    _add(
        Subscription(
            url=CHANNEL_URL,
            media_type=MediaType.AUDIO,
            audio_only=True,
            user_id=_user_id(authenticated_client),
        )
    )

    response = _subscribe(TestClient(app), _bearer(token))

    assert response.status_code == 201
    assert response.json() == {'task': 'DUPLICATE_SUBSCRIPTION'}
    assert submitted == []


# --- rate limits ---


def test_failed_token_attempts_are_limited_per_address(authenticated_client, monkeypatch):
    monkeypatch.setattr(EXTENSION_AUTH_FAIL_LIMITER, 'limit', 2)
    token = _create(authenticated_client)
    anonymous = TestClient(app)

    for _ in range(2):
        response = anonymous.get('/extension/me', headers=_bearer('ytdlh_not-a-real-token'))
        assert response.status_code == 401
    blocked = anonymous.get('/extension/me', headers=_bearer(token))

    assert blocked.status_code == 429
    assert int(blocked.headers['Retry-After']) >= 1


def test_each_token_has_its_own_budget(authenticated_client, monkeypatch):
    monkeypatch.setattr(EXTENSION_TOKEN_LIMITER, 'limit', 2)
    token = _create(authenticated_client)
    anonymous = TestClient(app)

    for _ in range(2):
        assert anonymous.get('/extension/me', headers=_bearer(token)).status_code == 200
    blocked = anonymous.get('/extension/me', headers=_bearer(token))

    assert blocked.status_code == 429
    assert int(blocked.headers['Retry-After']) >= 1


def test_polling_does_not_use_up_the_budget_for_starting_downloads(
    authenticated_client, submitted, monkeypatch
):
    monkeypatch.setattr(EXTENSION_POLL_LIMITER, 'limit', 2)
    token = _create(authenticated_client)
    anonymous = TestClient(app)

    for _ in range(2):
        assert anonymous.get('/extension/tasks', headers=_bearer(token)).status_code == 200
    blocked = anonymous.get('/extension/tasks', headers=_bearer(token))

    assert blocked.status_code == 429
    assert int(blocked.headers['Retry-After']) >= 1
    assert anonymous.get('/extension/me', headers=_bearer(token)).status_code == 200
    assert _download(anonymous, _bearer(token)).status_code == 201


def test_starting_downloads_does_not_use_up_the_polling_budget(
    authenticated_client, submitted, monkeypatch
):
    monkeypatch.setattr(EXTENSION_TOKEN_LIMITER, 'limit', 1)
    token = _create(authenticated_client)
    anonymous = TestClient(app)

    assert anonymous.get('/extension/me', headers=_bearer(token)).status_code == 200
    assert anonymous.get('/extension/me', headers=_bearer(token)).status_code == 429
    assert anonymous.get('/extension/tasks', headers=_bearer(token)).status_code == 200


def test_a_revoked_token_cannot_list_tasks(authenticated_client):
    token = _create(authenticated_client)
    token_id = authenticated_client.get('/auth/tokens').json()[0]['id']

    authenticated_client.delete(f'/auth/tokens/{token_id}')

    response = TestClient(app).get('/extension/tasks', headers=_bearer(token))
    assert response.status_code == 401
    assert response.headers['WWW-Authenticate'].startswith('Bearer')
