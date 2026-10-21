"""The transcript-only chain: stage captions (else audio), transcribe, keep nothing.

The download job only decides what to stage; the transcript job reads it by a name
derived from the URL alone, since startup recovery rebuilds that job from the database
row with no payload to carry a path in; and the hooks delete whatever was staged however
the transcript ends.
"""

import contextlib
import gzip
import json
import os
import time

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from config import settings
from database import db
from models import AppSettings, MediaDetails, MediaType, TaskStatus, User
from orchestrator import JobContext
from orchestrator.hooks import DownloadHooks, TranscriptHooks
from repositories import media_details as md_repo
from schemas import DownloadJobDTO, MediaDetailsDTO
from services.cleanup import cleanup_orphaned_thumbnails, cleanup_orphaned_transcript_staging
from services.transcript import create_transcript_blocks
from tasks import downloads
from ytdlp.files import (
    delete_transcript_staging,
    find_staged_audio,
    kept_thumbnail_path,
    kept_thumbnails_dir,
    staged_thumbnail_path,
    thumbnail_source_url,
    transcript_staging_base,
)
from ytdlp.options import create_ydl_options
from ytdlp.urls import get_url_hash

URL = 'https://www.youtube.com/watch?v=transcrOnly'
CAPTIONS = {'events': [{'tStartMs': 0, 'dDurationMs': 2000, 'segs': [{'utf8': 'hello there'}]}]}
SUBTITLE_ERROR = DownloadError("ERROR: Unable to download video subtitles for 'en': HTTP 429")
MEDIA_ERROR = DownloadError('ERROR: unable to download video data: HTTP Error 403')


@pytest.fixture
def staging(monkeypatch, tmp_path):
    monkeypatch.setattr(settings.storage, 'audio_path', str(tmp_path))
    base = transcript_staging_base(URL)
    os.makedirs(os.path.dirname(base), exist_ok=True)
    return base


@pytest.fixture
def app_settings(monkeypatch):
    current = AppSettings()
    for module in ('tasks.downloads', 'ytdlp.options', 'services.transcript'):
        monkeypatch.setattr(f'{module}.settings_repo.sync_get_settings', lambda: current)
    return current


@pytest.fixture
def media(clean_database) -> MediaDetails:
    session = db.get_sync_session()
    try:
        user = User(username='indexer', password_hash='x', is_approved=True)
        session.add(user)
        session.commit()
        row = MediaDetails(
            url=URL,
            media_type=MediaType.TRANSCRIPT,
            title='A Talk',
            channel='Lectures',
            status=TaskStatus.QUEUED,
            owner_id=user.id,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    finally:
        session.close()


def _write_captions(base: str, body: dict | str = CAPTIONS) -> str:
    path = f'{base}.en.json3'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(body if isinstance(body, str) else json.dumps(body))
    return path


# ------------------------------------------------------------------ option building


@pytest.fixture
def options(monkeypatch, staging, app_settings):
    def build(**kwargs):
        dto = DownloadJobDTO(
            url=URL, media_type=MediaType.TRANSCRIPT, audio_only=True, generate_transcript=True
        )
        return create_ydl_options(dto, sub_directory='.transcript-only', **kwargs)

    return build


def test_captions_pass_fetches_captions_and_nothing_else(options):
    opts = options(captions_only=True)

    assert opts['skip_download'] is True
    assert opts['writesubtitles'] is True
    assert opts['writeautomaticsub'] is True
    assert opts['subtitlesformat'] == 'json3/vtt'
    # Formats don't matter for captions, so a video whose formats come back empty
    # must not fail the pass.
    assert opts['ignore_no_formats_error'] is True
    assert opts['postprocessors'] == []
    # The captions pass's extract_info result is what the non-English warning reads, so it
    # must select exactly as the audio pass will.
    assert (opts['format'], opts['format_sort']) == (options()['format'], options()['format_sort'])


def test_whisper_fallback_takes_its_stream_as_is(options):
    opts = options()

    assert 'skip_download' not in opts
    assert 'writesubtitles' not in opts
    assert opts['postprocessors'] == [], 'no re-encode: Whisper resamples it anyway'
    assert opts['save_path'].endswith('.transcript-only')


def _audio(format_id: str, size: int, language: str | None = None, pref: int | None = None):
    return {
        'format_id': format_id,
        'url': f'https://example.invalid/{format_id}',
        'ext': 'm4a',
        'protocol': 'https',
        'vcodec': 'none',
        'acodec': 'mp4a.40.2',
        'filesize': size,
        'language': language,
        'language_preference': pref,
    }


def _muxed(format_id: str, size: int, language: str, pref: int | None = None):
    return {**_audio(format_id, size, language, pref), 'ext': 'mp4', 'vcodec': 'avc1.4d401e'}


def _hls(format_id: str, language: str, pref: int | None = None):
    """An HLS audio stream as YouTube lists it: no size or bitrate to sort on."""
    hls = {**_audio(format_id, 0, language, pref), 'protocol': 'm3u8_native', 'ext': 'mp4'}
    del hls['filesize']
    return hls


# YouTube marks the original track language_preference 10 and each auto-dub -1.
@pytest.mark.parametrize(
    ('formats', 'expected'),
    [
        pytest.param(
            [
                _audio('dub-ar', 1_000, 'ar', -1),
                _audio('orig-big', 9_000, 'en-US', 10),
                _audio('orig', 5_000, 'en-US', 10),
                _audio('dub-ml', 800, 'ml', -1),
            ],
            'orig',
            id='english-original-over-smaller-dubs',
        ),
        pytest.param(
            [
                _audio('orig', 1_000, 'de', 10),
                _audio('dub-en', 5_000, 'en', -1),
                _audio('dub-fr', 900, 'fr', -1),
            ],
            'dub-en',
            id='english-dub-over-non-english-original',
        ),
        pytest.param(
            [_audio('orig', 1_000, 'de', 10), _muxed('muxed-en', 50_000, 'en', -1)],
            'muxed-en',
            id='english-muxed-over-non-english-audio-only',
        ),
        pytest.param(
            [
                _audio('orig', 5_000, 'de', 10),
                _audio('orig-small', 3_000, 'de', 10),
                _audio('dub-fr', 900, 'fr', -1),
            ],
            'orig-small',
            id='no-english-takes-the-smallest-original',
        ),
        pytest.param(
            [_audio('big', 9_000), _audio('small', 2_000)],
            'small',
            id='no-language-metadata-takes-the-smallest',
        ),
        pytest.param(
            [_audio('https-en', 1_000, 'en-US', 10), _hls('hls-en', 'en-US', 10)],
            'hls-en',
            id='english-hls-over-smaller-english-https',
        ),
        pytest.param(
            [_hls('hls-ar', 'ar', -1), _hls('hls-en', 'en-US', 10)],
            'hls-en',
            id='english-original-hls-over-a-dubbed-hls',
        ),
        pytest.param(
            [_hls('hls-de', 'de', 10), _audio('https-en', 5_000, 'en', -1)],
            'https-en',
            id='english-https-over-non-english-hls',
        ),
    ],
)
def test_whisper_fallback_never_leaves_english_while_any_exists(options, formats, expected):
    opts = options()
    params = {'format': opts['format'], 'format_sort': opts['format_sort'], 'quiet': True}
    info = {'id': 'pick', 'title': 'pick', 'webpage_url': URL, 'formats': formats}

    with YoutubeDL(params) as ydl:
        picked = ydl.process_ie_result(info, download=False)

    assert picked['format_id'] == expected


# ----------------------------------------------------------------- the download job


class _Scenario:
    """Drives the fake YoutubeDL: what extract_info returns, what the passes write."""

    def __init__(self, base):
        self.base = base
        self.info = {'id': 'transcrOnly', 'live_status': 'not_live'}
        self.on_captions = lambda: None
        self.downloads = 0
        self.processed = []
        self.thumbnails_fetched = []

    def write_audio(self, ydl):
        path = f'{self.base}.webm'
        with open(path, 'wb') as f:
            f.write(b'audio')
        for pp in ydl.post_processors:
            pp.run({'filepath': path})
        self.downloads += 1


@pytest.fixture
def scenario(monkeypatch, staging, app_settings, media):
    scene = _Scenario(staging)

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts
            self.post_processors = []

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def add_post_processor(self, pp):
            self.post_processors.append(pp)

        def extract_info(self, _url, download=False):
            return scene.info

        def process_ie_result(self, _info, download=True):
            scene.processed.append(self.opts.get('skip_download', False))
            if self.opts.get('skip_download'):
                scene.on_captions()
            else:
                scene.write_audio(self)

        def download(self, _urls):
            scene.write_audio(self)

    def fake_save_thumbnail(image_url, dest):
        scene.thumbnails_fetched.append(image_url)
        with open(dest, 'wb') as f:
            f.write(b'jpeg')
        return dest

    monkeypatch.setattr(downloads, 'YoutubeDL', FakeYoutubeDL)
    monkeypatch.setattr(downloads, 'cookie_session', lambda **_k: contextlib.nullcontext(None))
    monkeypatch.setattr(downloads, 'save_thumbnail', fake_save_thumbnail)
    return scene


def _run(media: MediaDetails) -> tuple[dict, JobContext]:
    dto = DownloadJobDTO(
        url=URL,
        media_type=MediaType.TRANSCRIPT,
        audio_only=True,
        generate_transcript=True,
        media_details=MediaDetailsDTO.model_validate(media),
        user_id=media.owner_id,
    )
    ctx = JobContext(task_id='transcript-only-download', user_id=media.owner_id)
    return downloads.run_download_job(ctx, dto.model_dump(mode='json')), ctx


def _row(media_id: int) -> MediaDetails:
    return md_repo.sync_get_media_details_by_id(media_id)


def test_captions_are_staged_and_no_media_is_downloaded(scenario, media):
    scenario.on_captions = lambda: _write_captions(scenario.base)

    result, ctx = _run(media)

    assert result['id'] == media.id
    assert scenario.downloads == 0
    assert os.path.isfile(f'{scenario.base}.en.json3')
    assert _row(media.id).file_path is None
    assert ctx.skip_downstream is False, 'the transcript job builds and embeds them'


def test_captions_are_narrowed_to_the_videos_own_english_track(scenario, media):
    own = {'ext': 'json3', 'url': 'https://www.youtube.com/api/timedtext?lang=en'}
    dub = {'ext': 'json3', 'url': 'https://www.youtube.com/api/timedtext?lang=uk&tlang=en'}
    scenario.info['automatic_captions'] = {'en': [dub, own]}
    scenario.on_captions = lambda: _write_captions(scenario.base)

    _run(media)

    assert scenario.info['automatic_captions']['en'] == [own]


def test_the_thumbnail_is_staged_with_them_until_the_transcript_lands(scenario, media):
    scenario.on_captions = lambda: _write_captions(scenario.base)

    _run(media)

    assert scenario.thumbnails_fetched == ['https://i.ytimg.com/vi/transcrOnly/hqdefault.jpg']
    assert os.path.isfile(staged_thumbnail_path(URL))
    assert _row(media.id).thumbnail_path is None


def test_thumbnails_come_small_from_youtube_else_from_the_extractor():
    info = {'thumbnail': 'https://cdn.example/big.jpg'}

    assert thumbnail_source_url(URL, info) == 'https://i.ytimg.com/vi/transcrOnly/hqdefault.jpg'
    assert thumbnail_source_url('https://vimeo.com/123', info) == 'https://cdn.example/big.jpg'
    assert thumbnail_source_url('https://vimeo.com/123', None) is None


@pytest.mark.parametrize(
    'captions',
    [
        pytest.param(lambda base: None, id='no-track'),
        pytest.param(lambda base: _write_captions(base, '{"events": []}'), id='empty-file'),
        pytest.param(lambda base: _write_captions(base, 'not json'), id='unparseable-file'),
    ],
)
def test_no_usable_captions_stage_audio_for_whisper(scenario, media, captions):
    scenario.on_captions = lambda: captions(scenario.base)

    _run(media)

    assert scenario.downloads == 1
    assert not os.path.exists(f'{scenario.base}.en.json3'), 'an unusable file must go'
    assert _row(media.id).file_path == f'{scenario.base}.webm'


def test_a_failed_caption_fetch_falls_back_to_whisper_not_a_retry(scenario, media):
    def fail():
        raise SUBTITLE_ERROR

    scenario.on_captions = fail

    _run(media)

    assert scenario.downloads == 1


def test_any_other_error_is_retried(scenario, media):
    def fail():
        raise MEDIA_ERROR

    scenario.on_captions = fail

    with pytest.raises(Exception, match='403'):
        _run(media)
    assert scenario.downloads == 0


def test_force_whisper_skips_the_captions_pass(scenario, media, app_settings):
    app_settings.force_whisper_transcription = True

    _run(media)

    assert scenario.processed == [False], 'one pass, and it downloaded audio'
    assert scenario.downloads == 1


def test_an_unreleased_video_defers_without_staging(scenario, media):
    scenario.info = {'id': 'transcrOnly', 'live_status': 'is_upcoming'}

    result, ctx = _run(media)

    assert result['status'] == TaskStatus.NOT_READY.value
    assert ctx.skip_downstream is True
    assert scenario.processed == []


def test_a_retry_reuses_what_an_earlier_attempt_staged(scenario, media):
    with open(f'{scenario.base}.m4a', 'wb') as f:
        f.write(b'audio')

    _run(media)

    assert scenario.processed == [], 'no extraction at all'
    assert _row(media.id).file_path == f'{scenario.base}.m4a'
    assert os.path.isfile(staged_thumbnail_path(URL)), 'the YouTube image needs no extraction'


# ------------------------------------------------------------- the transcript job


def test_staged_captions_build_a_transcript_with_no_file(staging, app_settings, media):
    _write_captions(staging)

    blocks = create_transcript_blocks(MediaDetails(**_row(media.id).model_dump()))

    assert [b.text for b in blocks] == ['hello there']
    assert blocks[0].transcript_model == 'yt-dlp-subtitles-json3'


def test_neither_captions_nor_a_file_is_an_error_not_an_empty_transcript(
    staging, app_settings, media
):
    with pytest.raises(FileNotFoundError):
        create_transcript_blocks(MediaDetails(**_row(media.id).model_dump()))


# ------------------------------------------------------------------------- hooks


def _stage_everything(base: str) -> None:
    _write_captions(base)
    for suffix in ('.webm', '.whisper.json.gz', '.thumb.jpg'):
        with open(f'{base}{suffix}', 'wb') as f:
            f.write(gzip.compress(b'x'))


def _md_args(media_id: int) -> list[dict]:
    return [md_repo.sync_get_media_details_by_id(media_id).model_dump(mode='json')]


def test_a_finished_download_waits_for_its_transcript(media):
    retval = {'id': media.id, 'media_type': MediaType.TRANSCRIPT.value, 'url': URL}

    DownloadHooks().on_success(retval, 'dl-task', [{'url': URL}], {})

    assert _row(media.id).status == TaskStatus.POSTPROCESSING
    assert _row(media.id).downloaded_at is None


@pytest.mark.parametrize(
    ('end', 'status'),
    [
        ('success', TaskStatus.COMPLETE),
        ('failure', TaskStatus.FAILED),
        ('cancel', TaskStatus.CANCELLED),
    ],
)
def test_every_transcript_ending_deletes_what_was_staged(staging, media, end, status):
    md_repo.sync_update_one(
        media.id, {'status': TaskStatus.POSTPROCESSING, 'file_path': f'{staging}.webm'}
    )
    _stage_everything(staging)
    hooks = TranscriptHooks()
    args = _md_args(media.id)

    if end == 'success':
        hooks.on_success(None, 'tr-task', args, {})
    elif end == 'failure':
        hooks.on_failure(RuntimeError('boom'), 'tr-task', args, {})
    else:
        hooks.on_cancel('tr-task', args)

    assert os.listdir(os.path.dirname(staging)) == []
    row = _row(media.id)
    assert row.status == status
    assert row.file_path is None
    assert (row.downloaded_at is not None) == (status == TaskStatus.COMPLETE)
    # The thumbnail outlives the staging only when there is a transcript to show it with.
    kept = kept_thumbnail_path(URL, MediaType.TRANSCRIPT)
    if status == TaskStatus.COMPLETE:
        assert row.thumbnail_path == kept
        assert os.path.isfile(kept)
    else:
        assert row.thumbnail_path is None
        assert not os.path.exists(kept)


def test_a_retry_keeps_what_was_staged(staging, media):
    md_repo.sync_update_one(media.id, {'status': TaskStatus.POSTPROCESSING})
    _stage_everything(staging)

    TranscriptHooks().on_retry(RuntimeError('transient'), 'tr-task', _md_args(media.id), {})

    assert find_staged_audio(URL) is not None


def test_a_late_hook_cannot_knock_a_complete_row_back(staging, media):
    kept = kept_thumbnail_path(URL, MediaType.TRANSCRIPT)
    md_repo.sync_update_one(media.id, {'status': TaskStatus.COMPLETE, 'thumbnail_path': kept})

    TranscriptHooks().on_failure(RuntimeError('late'), 'tr-task', _md_args(media.id), {})

    assert _row(media.id).status == TaskStatus.COMPLETE
    assert _row(media.id).thumbnail_path == kept


def test_library_media_is_left_alone_by_the_transcript_hooks(staging, media):
    md_repo.sync_update_one(media.id, {'status': TaskStatus.POSTPROCESSING})
    _stage_everything(staging)
    args = _md_args(media.id)
    args[0]['media_type'] = MediaType.AUDIO.value

    TranscriptHooks().on_success(None, 'tr-task', args, {})

    assert find_staged_audio(URL) is not None
    assert _row(media.id).status == TaskStatus.POSTPROCESSING


def test_a_download_that_fails_for_good_keeps_nothing(staging, media):
    _stage_everything(staging)
    job = {'url': URL, 'media_type': MediaType.TRANSCRIPT.value}

    DownloadHooks().on_failure(RuntimeError('gone'), 'dl-task', [job], {})

    assert os.listdir(os.path.dirname(staging)) == []


# -------------------------------------------------------------------------- sweep


def test_sweep_spares_fresh_files_and_items_still_in_flight(staging):
    other = transcript_staging_base('https://www.youtube.com/watch?v=orphanedOne')
    stale = time.time() - 48 * 3600
    for path in (f'{staging}.webm', f'{other}.webm', f'{other}.en.json3'):
        with open(path, 'wb') as f:
            f.write(b'x')
        os.utime(path, (stale, stale))
    fresh = transcript_staging_base('https://www.youtube.com/watch?v=justStaged1')
    with open(f'{fresh}.webm', 'wb') as f:
        f.write(b'x')

    deleted = cleanup_orphaned_transcript_staging(24, in_flight_hashes={get_url_hash(URL)})

    assert deleted == 2
    remaining = sorted(os.listdir(os.path.dirname(staging)))
    assert remaining == sorted([f'{get_url_hash(URL)}.webm', f'{os.path.basename(fresh)}.webm'])


def test_staging_delete_only_touches_its_own_url(staging):
    neighbour = transcript_staging_base('https://www.youtube.com/watch?v=neighbour01')
    for path in (f'{staging}.webm', f'{neighbour}.webm'):
        with open(path, 'wb') as f:
            f.write(b'x')

    assert delete_transcript_staging(URL) == 1
    assert os.path.exists(f'{neighbour}.webm')


def _kept_file(url: str, media_type: MediaType, *, age_hours: float = 0) -> str:
    path = kept_thumbnail_path(url, media_type)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'jpeg')
    stamp = time.time() - age_hours * 3600
    os.utime(path, (stamp, stamp))
    return path


def test_thumbnail_sweep_deletes_only_old_files_no_row_points_at(staging, media):
    referenced = _kept_file(URL, MediaType.TRANSCRIPT, age_hours=48)
    md_repo.sync_update_one(media.id, {'thumbnail_path': referenced})
    orphan = _kept_file(URL, MediaType.VIDEO, age_hours=48)
    fresh = _kept_file(URL, MediaType.AUDIO)

    paths = md_repo.sync_get_thumbnail_paths_under(kept_thumbnails_dir())
    deleted = cleanup_orphaned_thumbnails(24, paths)

    assert paths == {referenced}
    assert deleted == 1
    assert not os.path.exists(orphan)
    assert os.path.isfile(referenced)
    assert os.path.isfile(fresh), 'may be mid-keep, its row not yet written'
