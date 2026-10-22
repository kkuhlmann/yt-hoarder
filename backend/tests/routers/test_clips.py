from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import update
from sqlmodel import select

from database import db
from models import Clip, MediaDetails, MediaType, TaskRecord, TaskStatus
from orchestrator import CLIP_FROM_SOURCE_JOB, CLIP_JOB


def _update_media(media_id: int, **values) -> None:
    session = db.get_sync_session()
    try:
        session.execute(update(MediaDetails).where(MediaDetails.id == media_id).values(**values))
        session.commit()
    finally:
        session.close()


def _set_media_file_path(media_id: int, file_path: str) -> None:
    """Set file_path on a MediaDetails row so the clip endpoint accepts it."""
    _update_media(media_id, file_path=file_path)


def _get_admin_id(client) -> int:
    users = client.get('/auth/users').json()
    return next(u for u in users if u['username'] == 'testadmin')['id']


async def test_create_clip_sets_task_record_user_id(monkeypatch, authenticated_client):
    """Creating a clip must persist the authenticated user as the TaskRecord owner."""
    # Replace orchestrator dispatch and progress publish with no-ops
    # (queue_sequence comes from the real Postgres sequence).
    monkeypatch.setattr('routers.clips.orch.submit', AsyncMock())
    monkeypatch.setattr('routers.clips.publish_status_change', MagicMock())

    # Clip endpoint requires source media to have a file on disk.
    _set_media_file_path(1, '/tmp/fake-source.m4a')

    admin_id = _get_admin_id(authenticated_client)

    response = authenticated_client.post(
        '/clips',
        json={
            'media_details_id': 1,
            'title': 'Test Clip',
            'start_time': 0.0,
            'end_time': 5.0,
        },
    )
    assert response.status_code == 201, response.text

    task_id = response.json()['task_id']

    session = db.get_sync_session()
    try:
        task_record = session.execute(
            select(TaskRecord).where(TaskRecord.task_id == task_id)
        ).scalar_one()
    finally:
        session.close()

    assert task_record.user_id == admin_id, (
        f'Expected TaskRecord.user_id={admin_id}, got {task_record.user_id}'
    )


# ============================================================
# Clips of items with no file, fetched from YouTube
# ============================================================

YOUTUBE_URL = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'


@pytest.fixture
def submitted(monkeypatch):
    """The orchestrator's submit, replaced so the JobSpec can be inspected."""
    submit = AsyncMock()
    monkeypatch.setattr('routers.clips.orch.submit', submit)
    monkeypatch.setattr('routers.clips.publish_status_change', MagicMock())
    return submit


def _post_clip(client, **extra):
    return client.post(
        '/clips',
        json={'media_details_id': 1, 'title': 'Clip', 'start_time': 1.0, 'end_time': 6.0, **extra},
    )


def _clip_and_task(clip_id: int, task_id: str) -> tuple[Clip, TaskRecord]:
    session = db.get_sync_session()
    try:
        clip = session.execute(select(Clip).where(Clip.id == clip_id)).scalar_one()
        task = session.execute(select(TaskRecord).where(TaskRecord.task_id == task_id)).scalar_one()
        return clip, task
    finally:
        session.close()


@pytest.mark.parametrize('output', [MediaType.VIDEO, MediaType.AUDIO])
def test_transcript_only_item_is_clipped_from_youtube(authenticated_client, submitted, output):
    _update_media(1, media_type=MediaType.TRANSCRIPT, file_path=None)

    response = _post_clip(authenticated_client, media_type=output.value)

    assert response.status_code == 201, response.text
    spec = submitted.await_args.args[0]
    assert spec.job_name == CLIP_FROM_SOURCE_JOB
    payload = spec.args[0]
    assert payload['source_url'] == YOUTUBE_URL
    assert payload['media_type'] == output.value
    clip, task = _clip_and_task(response.json()['id'], response.json()['task_id'])
    assert clip.media_type == output
    assert task.media_type == output


def test_staged_whisper_audio_is_never_clipped(authenticated_client, submitted):
    """Mid-chain, a transcript-only row's file_path is the audio staged for Whisper."""
    _update_media(
        1,
        media_type=MediaType.TRANSCRIPT,
        status=TaskStatus.POSTPROCESSING,
        file_path='/data/audio/.transcript-only/abc.m4a',
    )

    response = _post_clip(authenticated_client, media_type='AUDIO')

    assert response.status_code == 201, response.text
    assert submitted.await_args.args[0].job_name == CLIP_FROM_SOURCE_JOB


def test_deleted_media_that_kept_its_transcript_is_clipped_from_youtube(
    authenticated_client, submitted
):
    _update_media(1, status=TaskStatus.DELETED, file_path=None)

    response = _post_clip(authenticated_client, media_type='VIDEO')

    assert response.status_code == 201, response.text
    assert submitted.await_args.args[0].job_name == CLIP_FROM_SOURCE_JOB


def test_no_file_clip_requires_an_output_type(authenticated_client, submitted):
    _update_media(1, media_type=MediaType.TRANSCRIPT, file_path=None)

    response = _post_clip(authenticated_client)

    assert response.status_code == 400
    assert 'audio or video' in response.json()['detail']
    submitted.assert_not_awaited()


def test_no_file_clip_rejects_a_transcript_output(authenticated_client, submitted):
    _update_media(1, media_type=MediaType.TRANSCRIPT, file_path=None)

    assert _post_clip(authenticated_client, media_type='TRANSCRIPT').status_code == 400


def test_no_file_clip_requires_a_youtube_source(authenticated_client, submitted):
    _update_media(
        1, media_type=MediaType.TRANSCRIPT, file_path=None, url='https://vimeo.com/76979871'
    )

    response = _post_clip(authenticated_client, media_type='VIDEO')

    assert response.status_code == 400
    assert 'YouTube' in response.json()['detail']
    submitted.assert_not_awaited()


def test_local_clip_is_cut_from_the_file(authenticated_client, submitted):
    _set_media_file_path(1, '/tmp/fake-source.m4a')

    response = _post_clip(authenticated_client)

    assert response.status_code == 201, response.text
    spec = submitted.await_args.args[0]
    assert spec.job_name == CLIP_JOB
    assert 'source_url' not in spec.args[0]
    clip, _task = _clip_and_task(response.json()['id'], response.json()['task_id'])
    assert clip.media_type == MediaType.AUDIO


def test_local_clip_rejects_a_different_output_type(authenticated_client, submitted):
    """A file is cut as what it is; the field must not be silently ignored."""
    _set_media_file_path(1, '/tmp/fake-source.m4a')

    response = _post_clip(authenticated_client, media_type='VIDEO')

    assert response.status_code == 400
    submitted.assert_not_awaited()


def _insert_clip_of(media_details_id: int, user_id: int) -> int:
    session = db.get_sync_session()
    try:
        clip = Clip(
            media_details_id=media_details_id,
            title='Sourced',
            start_time=12.0,
            end_time=20.0,
            media_type=MediaType.AUDIO,
            status=TaskStatus.COMPLETE,
            user_id=user_id,
        )
        session.add(clip)
        session.commit()
        return clip.id
    finally:
        session.close()


def _listed_clip(client, clip_id: int) -> dict:
    records = client.get('/clips').json()['records']
    return next(r for r in records if r['id'] == clip_id)


def test_clip_list_marks_a_source_with_no_file(authenticated_client):
    _update_media(1, media_type=MediaType.TRANSCRIPT, file_path=None)
    clip_id = _insert_clip_of(1, _get_admin_id(authenticated_client))

    record = _listed_clip(authenticated_client, clip_id)

    assert record['source_url'] == YOUTUBE_URL
    assert record['source_playable'] is False


def test_clip_list_marks_a_source_the_app_holds(authenticated_client):
    _set_media_file_path(1, '/tmp/fake-source.m4a')
    clip_id = _insert_clip_of(1, _get_admin_id(authenticated_client))

    assert _listed_clip(authenticated_client, clip_id)['source_playable'] is True


def test_clip_list_handles_a_clip_whose_source_is_gone(authenticated_client):
    clip_id = _insert_clip(None, user_id=_get_admin_id(authenticated_client))

    record = _listed_clip(authenticated_client, clip_id)

    assert record['source_url'] is None
    assert record['source_playable'] is False


def _insert_clip(
    file_path: str | None,
    *,
    user_id: int,
    title: str = 'Best*Clip:Mix',
    media_type: MediaType = MediaType.AUDIO,
    status_: TaskStatus = TaskStatus.COMPLETE,
) -> int:
    """Insert a clip row directly and return its id."""
    session = db.get_sync_session()
    try:
        clip = Clip(
            media_details_id=None,
            title=title,
            start_time=0.0,
            end_time=5.0,
            duration=5.0,
            file_path=file_path,
            media_type=media_type,
            status=status_,
            user_id=user_id,
        )
        session.add(clip)
        session.commit()
        session.refresh(clip)
        return clip.id
    finally:
        session.close()


async def test_download_clip_returns_audio_attachment(tmp_path, authenticated_client):
    """Downloading an audio clip returns the file as an attachment with a sanitized name."""
    admin_id = _get_admin_id(authenticated_client)
    clip_file = tmp_path / 'clip_audio.mp3'
    clip_file.write_bytes(b'ID3-fake-mp3-bytes')
    # Title has filesystem-unsafe characters ':' and '*' that must be stripped.
    clip_id = _insert_clip(str(clip_file), user_id=admin_id, title='Best*Clip:Mix')

    response = authenticated_client.get(f'/media/clip/{clip_id}/download')

    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('audio/mpeg')
    content_disposition = response.headers['content-disposition']
    assert 'attachment' in content_disposition
    assert 'BestClipMix.mp3' in content_disposition  # ':' and '*' removed
    assert response.content == b'ID3-fake-mp3-bytes'


async def test_download_clip_returns_video_attachment(tmp_path, authenticated_client):
    """Downloading a video clip returns an .mp4 attachment with video/mp4 type."""
    admin_id = _get_admin_id(authenticated_client)
    clip_file = tmp_path / 'clip_video.mp4'
    clip_file.write_bytes(b'fake-mp4-bytes')
    clip_id = _insert_clip(
        str(clip_file), user_id=admin_id, title='HighlightReel', media_type=MediaType.VIDEO
    )

    response = authenticated_client.get(f'/media/clip/{clip_id}/download')

    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('video/mp4')
    content_disposition = response.headers['content-disposition']
    assert 'attachment' in content_disposition
    assert 'HighlightReel.mp4' in content_disposition


async def test_download_clip_missing_file_returns_404(authenticated_client):
    """A clip whose file is absent from disk returns 404 with a clip-specific detail."""
    admin_id = _get_admin_id(authenticated_client)
    clip_id = _insert_clip('/tmp/does-not-exist-clip.mp3', user_id=admin_id)

    response = authenticated_client.get(f'/media/clip/{clip_id}/download')

    assert response.status_code == 404
    assert response.json()['detail'] == f'Clip file not found for clip with id {clip_id}'


# ============================================================
# Sharing endpoints (admin_view-gated admin override)
# ============================================================


def _setup_two_users(client):
    """Register admin (first user) + second non-admin user. Return (admin_id, user2_id, client2)."""
    from fastapi.testclient import TestClient

    from main import app

    admin_id = _get_admin_id(client)

    reg_resp = client.post('/auth/register', json={'username': 'user2', 'password': 'pass123'})
    user2_id = reg_resp.json()['id']
    client.post(f'/auth/users/{user2_id}/approve')

    client2 = TestClient(app)
    client2.post('/auth/login', json={'username': 'user2', 'password': 'pass123'})

    return admin_id, user2_id, client2


def test_owner_can_share_and_unshare_clip(authenticated_client):
    """The clip owner can share, list shared users, and unshare without admin_view."""
    admin_id, user2_id, client2 = _setup_two_users(authenticated_client)
    clip_id = _insert_clip('/tmp/share-test.mp3', user_id=admin_id)

    assert client2.get(f'/clips/{clip_id}').status_code == 404

    resp = authenticated_client.post(f'/clips/{clip_id}/share', json={'user_id': user2_id})
    assert resp.status_code == 201, resp.text

    assert client2.get(f'/clips/{clip_id}').status_code == 200

    resp = authenticated_client.get(f'/clips/{clip_id}/shared-users')
    assert resp.status_code == 200
    assert resp.json()['shared_user_ids'] == [user2_id]

    resp = authenticated_client.delete(f'/clips/{clip_id}/share/{user2_id}')
    assert resp.status_code == 204
    assert client2.get(f'/clips/{clip_id}').status_code == 404


def test_non_owner_cannot_manage_clip_sharing(authenticated_client):
    """A non-owner (even with shared access) cannot share/unshare/list shared users."""
    admin_id, user2_id, client2 = _setup_two_users(authenticated_client)
    clip_id = _insert_clip('/tmp/share-test.mp3', user_id=admin_id)
    authenticated_client.post(f'/clips/{clip_id}/share', json={'user_id': user2_id})

    assert client2.post(f'/clips/{clip_id}/share', json={'user_id': user2_id}).status_code == 404
    assert client2.get(f'/clips/{clip_id}/shared-users').status_code == 404
    assert client2.delete(f'/clips/{clip_id}/share/{user2_id}').status_code == 404


def test_admin_clip_sharing_requires_admin_view(authenticated_client):
    """Admin managing sharing on a clip they don't own must opt in via admin_view=true."""
    admin_id, user2_id, _client2 = _setup_two_users(authenticated_client)
    clip_id = _insert_clip('/tmp/share-test.mp3', user_id=user2_id)

    # Without admin_view, the admin is treated as a plain non-owner → 404
    resp = authenticated_client.post(f'/clips/{clip_id}/share', json={'user_id': admin_id})
    assert resp.status_code == 404
    assert authenticated_client.get(f'/clips/{clip_id}/shared-users').status_code == 404
    assert authenticated_client.delete(f'/clips/{clip_id}/share/{admin_id}').status_code == 404

    # With admin_view=true, the admin override applies
    resp = authenticated_client.post(
        f'/clips/{clip_id}/share?admin_view=true', json={'user_id': admin_id}
    )
    assert resp.status_code == 201, resp.text

    resp = authenticated_client.get(f'/clips/{clip_id}/shared-users?admin_view=true')
    assert resp.status_code == 200
    assert resp.json()['shared_user_ids'] == [admin_id]

    resp = authenticated_client.delete(f'/clips/{clip_id}/share/{admin_id}?admin_view=true')
    assert resp.status_code == 204
