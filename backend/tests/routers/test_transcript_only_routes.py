"""The API surface of transcript-only media: requests, subscriptions, the transcript
viewer's read, and the delete / cancel / retry paths that must not strand a staged file
or an item in POSTPROCESSING.
"""

import os
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from config import settings
from database import db
from models import (
    DownloadJob,
    MediaAccess,
    MediaDetails,
    MediaType,
    Subscription,
    TaskRecord,
    TaskStatus,
    TaskType,
    TranscriptBlock,
    User,
)
from orchestrator import orch
from ytdlp.files import kept_thumbnail_path, transcript_staging_base

URL = 'https://www.youtube.com/watch?v=routesOnly1'


def _user_id(client) -> int:
    return client.get('/auth/me').json()['id']


def _add(*rows):
    session = db.get_sync_session()
    try:
        for row in rows:
            session.add(row)
            session.commit()
            session.refresh(row)
        return rows
    finally:
        session.close()


def _media(owner_id: int, *, media_type=MediaType.TRANSCRIPT, url=URL, **kw) -> MediaDetails:
    (md,) = _add(
        MediaDetails(
            url=url,
            media_type=media_type,
            title='A Talk',
            channel='Lectures',
            status=kw.pop('status', TaskStatus.COMPLETE),
            owner_id=owner_id,
            **kw,
        )
    )
    _add(MediaAccess(user_id=owner_id, media_details_id=md.id))
    return md


def _blocks(media_id: int, *texts: str) -> None:
    _add(
        *(
            TranscriptBlock(
                media_details_id=media_id,
                start_time=float(10 * i),
                end_time=float(10 * i + 10),
                text=text,
            )
            for i, text in enumerate(texts)
        )
    )


def _get(model, row_id):
    session = db.get_sync_session()
    try:
        return session.get(model, row_id)
    finally:
        session.close()


@pytest.fixture
def submitted(monkeypatch):
    """The pipeline payloads POST /ytdl/ and POST /subscriptions hand the orchestrator."""
    calls = []

    async def record(spec):
        calls.append(spec.args[0])
        return 'job-id'

    monkeypatch.setattr('routers.ytdl_router.orch.submit', record)
    monkeypatch.setattr('routers.subscriptions.orch.submit', record)
    return calls


@pytest.fixture
def staging(monkeypatch, tmp_path):
    monkeypatch.setattr(settings.storage, 'audio_path', str(tmp_path))
    base = transcript_staging_base(URL)
    os.makedirs(os.path.dirname(base), exist_ok=True)
    return base


# ------------------------------------------------------------------------ POST /ytdl/


def _request(client, **overrides):
    body = {
        'url': URL,
        'media_type': 'TRANSCRIPT',
        'audio_only': False,
        'generate_transcript': False,
        **overrides,
    }
    return client.post('/ytdl/', json=body)


def test_a_transcript_only_request_implies_audio_and_a_transcript(authenticated_client, submitted):
    response = _request(authenticated_client)

    assert response.status_code == 201
    (jobs,) = submitted
    assert jobs[0]['media_type'] == 'TRANSCRIPT'
    assert jobs[0]['audio_only'] is True
    assert jobs[0]['generate_transcript'] is True


def test_requesting_what_the_library_already_transcribed_is_refused(
    authenticated_client, submitted
):
    video = _media(_user_id(authenticated_client), media_type=MediaType.VIDEO)
    _blocks(video.id, 'already searchable')

    response = _request(authenticated_client)

    assert response.status_code == 409
    assert 'in your Transcripts view' in response.json()['detail']['message']
    assert submitted == []


def test_an_untranscribed_library_copy_does_not_block_it(authenticated_client, submitted):
    _media(_user_id(authenticated_client), media_type=MediaType.VIDEO)

    assert _request(authenticated_client).status_code == 201


def test_the_storage_limit_does_not_apply(authenticated_client, submitted):
    session = db.get_sync_session()
    try:
        user = session.get(User, _user_id(authenticated_client))
        user.storage_limit_bytes = 0
        session.commit()
    finally:
        session.close()

    assert _request(authenticated_client, media_type='VIDEO').status_code == 403
    assert _request(authenticated_client).status_code == 201


# ---------------------------------------------------------------------- subscriptions


def _subscribe(client, media_type: str, audio_only: bool = False):
    return client.post(
        '/subscriptions',
        json={
            'url': 'https://www.youtube.com/@SomeChannel',
            'media_type': media_type,
            'audio_only': audio_only,
            'generate_transcript': False,
            'overwrite': False,
        },
    )


def _subscription(
    user_id: int, media_type: MediaType, audio_only: bool, string_match: str | None = None
) -> Subscription:
    (sub,) = _add(
        Subscription(
            url='https://www.youtube.com/@SomeChannel',
            media_type=media_type,
            audio_only=audio_only,
            string_match=string_match,
            generate_transcript=media_type == MediaType.TRANSCRIPT,
            user_id=user_id,
        )
    )
    return sub


def test_transcript_only_subscriptions_are_normalized(authenticated_client, submitted):
    response = _subscribe(authenticated_client, 'TRANSCRIPT')

    assert response.json()['task'] == 'job-id'
    (sub,) = submitted
    assert sub['audio_only'] is True
    assert sub['generate_transcript'] is True


def test_an_audio_subscription_does_not_count_as_a_transcript_only_duplicate(
    authenticated_client, submitted
):
    _subscription(_user_id(authenticated_client), MediaType.AUDIO, audio_only=True)

    assert _subscribe(authenticated_client, 'TRANSCRIPT').json()['task'] == 'job-id'
    assert _subscribe(authenticated_client, 'AUDIO', True).json()['task'] == (
        'DUPLICATE_SUBSCRIPTION'
    )


def test_a_transcript_only_subscription_keeps_its_mode_when_edited(authenticated_client):
    sub = _subscription(_user_id(authenticated_client), MediaType.TRANSCRIPT, audio_only=True)

    response = authenticated_client.put(
        f'/subscriptions/{sub.id}',
        json={'audio_only': False, 'generate_transcript': False, 'enabled': False},
    )

    assert response.status_code == 201
    edited = _get(Subscription, sub.id)
    assert (edited.audio_only, edited.generate_transcript, edited.enabled) == (True, True, False)
    assert edited.media_type == MediaType.TRANSCRIPT


def test_switching_audio_only_moves_media_type_with_it(authenticated_client):
    sub = _subscription(_user_id(authenticated_client), MediaType.VIDEO, audio_only=False)

    authenticated_client.put(f'/subscriptions/{sub.id}', json={'audio_only': True})

    assert _get(Subscription, sub.id).media_type == MediaType.AUDIO


def test_switching_onto_an_existing_subscription_is_a_conflict(authenticated_client):
    # The unique key treats NULL string_matches as distinct, as Postgres does, so the
    # collision needs a real one.
    user_id = _user_id(authenticated_client)
    _subscription(user_id, MediaType.AUDIO, audio_only=True, string_match='News')
    video = _subscription(user_id, MediaType.VIDEO, audio_only=False, string_match='News')

    response = authenticated_client.put(f'/subscriptions/{video.id}', json={'audio_only': True})

    assert response.status_code == 409


# --------------------------------------------------------------- the viewer's read


def test_the_transcript_reads_in_playback_order(authenticated_client):
    md = _media(_user_id(authenticated_client))
    _blocks(md.id, 'first', 'second', 'third')

    body = authenticated_client.get(f'/media-details/{md.id}/transcript').json()

    assert [b['text'] for b in body['blocks']] == ['first', 'second', 'third']
    assert body['youtube_id'] == 'routesOnly1'
    assert body['media']['playable'] is False
    assert body['media']['rating'] is None
    assert body['media']['access_count'] == 0
    assert body['media']['playback_position'] is None
    assert body['media']['owner_id'] == _user_id(authenticated_client)
    assert body['media']['transcript_task_status'] is None


def test_the_transcript_reports_a_running_transcription(authenticated_client):
    user_id = _user_id(authenticated_client)
    (task,) = _add(
        TaskRecord(
            task_id='running-transcript',
            task_type=TaskType.TRANSCRIPT_GENERATION,
            status=TaskStatus.IN_PROGRESS,
            percent_complete=42,
            user_id=user_id,
        )
    )
    md = _media(
        user_id,
        media_type=MediaType.VIDEO,
        file_path='/mnt/video/a.mp4',
        transcript_task_record_id=task.id,
    )

    media = authenticated_client.get(f'/media-details/{md.id}/transcript').json()['media']

    assert media['transcript_task_status'] == 'IN_PROGRESS'
    assert media['transcript_task_progress'] == 42


def test_the_transcript_carries_the_callers_rating(authenticated_client):
    md = _media(_user_id(authenticated_client))
    authenticated_client.put(f'/media-details/{md.id}/rating', json={'rating': 4})

    body = authenticated_client.get(f'/media-details/{md.id}/transcript').json()

    assert body['media']['rating'] == 4


def test_a_downloaded_file_is_playable(authenticated_client):
    md = _media(
        _user_id(authenticated_client), media_type=MediaType.VIDEO, file_path='/mnt/video/a.mp4'
    )

    body = authenticated_client.get(f'/media-details/{md.id}/transcript').json()

    assert body['media']['playable'] is True


def test_a_non_youtube_source_has_nothing_to_embed(authenticated_client):
    md = _media(_user_id(authenticated_client), url='https://vimeo.com/12345')

    assert (
        authenticated_client.get(f'/media-details/{md.id}/transcript').json()['youtube_id'] is None
    )


# --------------------------------------------------------------- delete and re-index


def test_deleting_a_transcript_only_item_always_drops_its_transcript(authenticated_client):
    md = _media(_user_id(authenticated_client))
    _blocks(md.id, 'gone')

    response = authenticated_client.delete(f'/media-details/{md.id}?keep_transcripts=true')

    assert response.status_code == 204
    session = db.get_sync_session()
    try:
        remaining = session.execute(
            select(TranscriptBlock).where(TranscriptBlock.media_details_id == md.id)
        ).all()
    finally:
        session.close()
    assert remaining == []
    assert _get(MediaDetails, md.id).status == TaskStatus.DELETED


# A thumbnail lives exactly as long as the media file or the transcript does.


def _write(path) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'x')
    return str(path)


@pytest.fixture
def downloaded(staging, tmp_path, authenticated_client):
    """A transcribed library video with its file and sidecar thumbnail on disk."""

    def make(*, transcribed: bool = True) -> MediaDetails:
        video = _write(tmp_path / 'video' / 'A Talk [abc].mp4')
        thumb = _write(tmp_path / 'video' / 'A Talk [abc].thumb.jpg')
        md = _media(
            _user_id(authenticated_client),
            media_type=MediaType.VIDEO,
            file_path=video,
            thumbnail_path=thumb,
        )
        if transcribed:
            _blocks(md.id, 'kept')
        return md

    return make


def test_deleting_but_keeping_the_transcript_keeps_the_thumbnail(authenticated_client, downloaded):
    md = downloaded()
    sidecar = md.thumbnail_path

    authenticated_client.delete(f'/media-details/{md.id}?keep_transcripts=true')

    kept = kept_thumbnail_path(URL, MediaType.VIDEO)
    assert _get(MediaDetails, md.id).thumbnail_path == kept
    assert os.path.isfile(kept)
    assert not os.path.exists(sidecar)
    assert not os.path.exists(md.file_path)


@pytest.mark.parametrize(
    ('keep', 'transcribed'),
    [
        pytest.param('false', True, id='transcript-dropped'),
        pytest.param('true', False, id='no-transcript-to-keep'),
    ],
)
def test_with_no_transcript_left_the_thumbnail_goes_with_the_file(
    authenticated_client, downloaded, keep, transcribed
):
    md = downloaded(transcribed=transcribed)

    authenticated_client.delete(f'/media-details/{md.id}?keep_transcripts={keep}')

    assert _get(MediaDetails, md.id).thumbnail_path is None
    assert not os.path.exists(md.thumbnail_path)
    assert not os.path.exists(kept_thumbnail_path(URL, MediaType.VIDEO))


def test_deleting_a_transcript_only_item_deletes_its_thumbnail(authenticated_client, staging):
    thumb = _write(kept_thumbnail_path(URL, MediaType.TRANSCRIPT))
    md = _media(_user_id(authenticated_client), thumbnail_path=thumb)
    _blocks(md.id, 'gone')

    authenticated_client.delete(f'/media-details/{md.id}?keep_transcripts=true')

    assert _get(MediaDetails, md.id).thumbnail_path is None
    assert not os.path.exists(thumb)


def test_deleting_the_transcript_of_a_deleted_video_deletes_its_thumbnail(
    authenticated_client, downloaded
):
    md = downloaded()
    authenticated_client.delete(f'/media-details/{md.id}?keep_transcripts=true')
    kept = _get(MediaDetails, md.id).thumbnail_path

    response = authenticated_client.delete(f'/media-details/{md.id}/transcripts')

    assert response.status_code == 200
    assert _get(MediaDetails, md.id).thumbnail_path is None
    assert not os.path.exists(kept)


def test_deleting_the_transcript_of_a_downloaded_video_keeps_its_thumbnail(
    authenticated_client, downloaded
):
    md = downloaded()

    authenticated_client.delete(f'/media-details/{md.id}/transcripts')

    assert _get(MediaDetails, md.id).thumbnail_path == md.thumbnail_path
    assert os.path.isfile(md.thumbnail_path)


def test_a_permanent_delete_deletes_the_kept_thumbnail(authenticated_client, downloaded):
    md = downloaded()
    authenticated_client.delete(f'/media-details/{md.id}?keep_transcripts=true')
    kept = _get(MediaDetails, md.id).thumbnail_path

    response = authenticated_client.delete(f'/media-details/{md.id}/hard')

    assert response.status_code == 204
    assert _get(MediaDetails, md.id) is None
    assert not os.path.exists(kept)


def test_there_is_nothing_to_re_transcribe_from(authenticated_client):
    md = _media(_user_id(authenticated_client))

    response = authenticated_client.post(f'/media-details/transcripts/{md.id}/create')

    assert response.status_code == 409


# ---------------------------------------------------------------- cancel and retry


def _chain(user_id: int, *, transcript_status: TaskStatus, md_status: TaskStatus):
    download, transcript = _add(
        TaskRecord(
            task_id='only-download',
            task_type=TaskType.DOWNLOAD,
            status=TaskStatus.COMPLETE,
            download_job_url=URL,
            media_type=MediaType.TRANSCRIPT,
            user_id=user_id,
        ),
        TaskRecord(
            task_id='only-transcript',
            task_type=TaskType.TRANSCRIPT_GENERATION,
            status=transcript_status,
            upstream_task_ids=['only-download'],
            download_job_url=URL,
            media_type=MediaType.TRANSCRIPT,
            user_id=user_id,
        ),
    )
    md = _media(
        user_id,
        status=md_status,
        download_task_record_id=download.id,
        transcript_task_record_id=transcript.id,
    )
    _add(
        DownloadJob(
            url=URL,
            audio_only=True,
            media_type=MediaType.TRANSCRIPT,
            generate_transcript=True,
            media_details_id=md.id,
            user_id=user_id,
        )
    )
    return download, transcript, md


def test_cancelling_a_queued_transcript_ends_the_item(monkeypatch, authenticated_client, staging):
    """No job ever started, so no hook will: the endpoint has to end it itself."""
    monkeypatch.setattr('routers.task_records.orch.cancel', AsyncMock(return_value='dequeued'))
    _, _, md = _chain(
        _user_id(authenticated_client),
        transcript_status=TaskStatus.QUEUED,
        md_status=TaskStatus.POSTPROCESSING,
    )
    with open(f'{staging}.en.json3', 'w') as f:
        f.write('{}')

    response = authenticated_client.delete('/tasks/only-transcript')

    assert response.status_code == 200
    assert os.listdir(os.path.dirname(staging)) == []
    assert _get(MediaDetails, md.id).status == TaskStatus.CANCELLED


def test_bulk_cancel_ends_the_item_too(monkeypatch, authenticated_client, staging):
    monkeypatch.setattr('routers.task_records.orch.cancel', AsyncMock(return_value='dequeued'))
    monkeypatch.setattr(orch, 'cancel', AsyncMock(return_value='dequeued'))
    _, _, md = _chain(
        _user_id(authenticated_client),
        transcript_status=TaskStatus.QUEUED,
        md_status=TaskStatus.POSTPROCESSING,
    )
    with open(f'{staging}.webm', 'wb') as f:
        f.write(b'audio')

    response = authenticated_client.post(
        '/tasks/bulk/cancel', json={'task_ids': ['only-transcript']}
    )

    assert response.status_code == 200
    assert os.listdir(os.path.dirname(staging)) == []
    assert _get(MediaDetails, md.id).status == TaskStatus.CANCELLED


def test_retrying_a_failed_transcript_re_runs_its_download(
    monkeypatch, authenticated_client, staging
):
    """Its staged captions or audio were deleted when it failed, so the transcript job
    alone could only fail again."""
    monkeypatch.setattr(orch, 'cancel', AsyncMock(return_value='unknown'))
    dispatched = []
    monkeypatch.setattr(
        'repositories.task_records.retry.dispatch_download_chain',
        lambda **kwargs: dispatched.append(kwargs),
    )
    download, transcript, _ = _chain(
        _user_id(authenticated_client),
        transcript_status=TaskStatus.FAILED,
        md_status=TaskStatus.FAILED,
    )

    response = authenticated_client.post(
        f'/tasks/{transcript.id}/retry', json={'retry_downstream': False, 'overwrite': False}
    )

    assert response.status_code == 200, response.text
    (call,) = dispatched
    rearmed_download = _get(TaskRecord, download.id)
    rearmed_transcript = _get(TaskRecord, transcript.id)
    assert call['download_task_id'] == rearmed_download.task_id != 'only-download'
    assert call['transcript_task_id'] == rearmed_transcript.task_id != 'only-transcript'
    assert call['download_data']['media_type'] == 'TRANSCRIPT'
    assert rearmed_download.status == TaskStatus.QUEUED
    assert rearmed_transcript.upstream_task_ids == [rearmed_download.task_id]


def test_retrying_with_its_files_still_staged_retries_just_the_transcript(
    monkeypatch, authenticated_client, staging
):
    monkeypatch.setattr(orch, 'cancel', AsyncMock(return_value='unknown'))
    transcript_dispatches = []
    monkeypatch.setattr(
        'repositories.task_records.retry._dispatch_transcript_task',
        lambda *args, **kwargs: transcript_dispatches.append(args),
    )
    _, transcript, _ = _chain(
        _user_id(authenticated_client),
        transcript_status=TaskStatus.RETRY,
        md_status=TaskStatus.POSTPROCESSING,
    )
    with open(f'{staging}.webm', 'wb') as f:
        f.write(b'audio')

    response = authenticated_client.post(
        f'/tasks/{transcript.id}/retry', json={'retry_downstream': False, 'overwrite': False}
    )

    assert response.status_code == 200, response.text
    assert len(transcript_dispatches) == 1


def test_watching_in_the_viewer_shows_as_last_watched(authenticated_client):
    """The viewer's YouTube embed records playback through the same endpoint as the
    app's own player, which is all the Transcripts view's Last Watched column reads."""
    md = _media(_user_id(authenticated_client))
    _blocks(md.id, 'watched')

    authenticated_client.patch(
        f'/media-details/{md.id}/playback',
        json={'playback_position': 42.0, 'last_accessed': '2026-10-19T12:00:00Z'},
    )

    rows = authenticated_client.get('/media-details?has_transcript=true').json()['records']
    (row,) = [r for r in rows if r['id'] == md.id]
    assert row['last_accessed'] is not None
    assert row['playback_position'] == 42.0
    assert row['access_count'] == 1
    # And the viewer reads it back to resume from.
    transcript = authenticated_client.get(f'/media-details/{md.id}/transcript').json()
    assert transcript['media']['playback_position'] == 42.0
