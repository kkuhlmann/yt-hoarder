"""A caption track that fails to download must not fail the media download.

yt-dlp raises from _write_subtitles when a listed track's fetch fails (a 429 from the
caption endpoint), before any media bytes move. Left alone, that DownloadError retried
the whole download until it ended FAILED, and the transcript never reached Whisper.
"""

import contextlib
from typing import ClassVar
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from models import AppSettings, MediaType
from orchestrator import JobContext
from schemas import DownloadJobDTO, MediaDetailsDTO
from tasks.downloads import _download_with_fallback
from ytdlp.options import YtDlLogger, create_ydl_options
from ytdlp.subtitles import is_subtitle_download_error, prefer_untranslated_captions

URL = 'https://www.youtube.com/watch?v=subFallbk01'

# Verbatim shape of what YoutubeDL.report_error raises (yt-dlp 2026.08.19).
SUBTITLE_ERROR = DownloadError(
    "ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests"
)
FORMAT_ERROR = DownloadError('ERROR: [youtube] x: Requested format is not available')


def test_matches_only_the_subtitle_download_error():
    assert is_subtitle_download_error(SUBTITLE_ERROR)
    assert not is_subtitle_download_error(FORMAT_ERROR)
    assert not is_subtitle_download_error(
        ValueError('Unable to download video subtitles for "en"')
    ), 'only a yt-dlp DownloadError counts'


# ----------------------------------------------------------------- option building


@pytest.fixture
def stubbed_environment(monkeypatch):
    monkeypatch.setattr('ytdlp.options.settings_repo.sync_get_settings', lambda: AppSettings())
    monkeypatch.setattr('ytdlp.options.os.makedirs', lambda *a, **k: None)


def test_transcript_jobs_request_json3_then_vtt(stubbed_environment):
    options = create_ydl_options(DownloadJobDTO(url=URL, generate_transcript=True))
    assert options['writesubtitles'] is True
    assert options['writeautomaticsub'] is True
    assert options['subtitlesformat'] == 'json3/vtt'


def test_with_subtitles_false_leaves_captions_out(stubbed_environment):
    options = create_ydl_options(
        DownloadJobDTO(url=URL, generate_transcript=True), with_subtitles=False
    )
    assert 'writesubtitles' not in options
    assert 'writeautomaticsub' not in options


def test_caption_failure_is_logged_as_a_warning(monkeypatch):
    fake_logger = MagicMock()
    monkeypatch.setattr('ytdlp.options.logger', fake_logger)

    YtDlLogger().error(str(SUBTITLE_ERROR))
    YtDlLogger().error('ERROR: something else broke')

    fake_logger.warning.assert_called_once()
    fake_logger.error.assert_called_once_with('ERROR: something else broke')


# --------------------------------------------------------- which English track is fetched


def _caption(lang: str, tlang: str | None = None) -> dict:
    query = f'v=x&kind=asr&lang={lang}&fmt=json3' + (f'&tlang={tlang}' if tlang else '')
    return {
        'ext': 'json3',
        'name': 'English',
        'url': f'https://www.youtube.com/api/timedtext?{query}',
    }


# A dubbed English video as yt-dlp lists it: every audio track has an auto-caption track,
# and each but the original's is filed under 'en' as a translation into English.
ORIGINAL = _caption('en')
DUBBED = [_caption('ar', tlang='en'), ORIGINAL, _caption('uk', tlang='en')]


def _fetched(stubbed_environment, captions: list[dict], *, narrow: bool = True) -> dict:
    """The query of the English caption URL the app's real options make yt-dlp fetch."""
    options = create_ydl_options(DownloadJobDTO(url=URL, generate_transcript=True))
    subtitle_options = {k: v for k, v in options.items() if 'sub' in k}
    info = {
        'id': 'x',
        'title': 'x',
        'webpage_url': URL,
        'formats': [{'format_id': 'a', 'url': 'https://example.invalid/a', 'ext': 'm4a'}],
        'automatic_captions': {'en': list(captions)},
    }
    if narrow:
        prefer_untranslated_captions(info)
    with YoutubeDL({**subtitle_options, 'quiet': True}) as ydl:
        picked = ydl.process_ie_result(info, download=False)
    return parse_qs(urlparse(picked['requested_subtitles']['en']['url']).query)


def test_left_to_itself_yt_dlp_fetches_a_dubs_translation(stubbed_environment):
    """Why the narrowing exists: YouTube 429s the translated track yt-dlp picks."""
    assert _fetched(stubbed_environment, DUBBED, narrow=False)['tlang'] == ['en']


def test_a_dubbed_videos_own_english_captions_are_fetched(stubbed_environment):
    query = _fetched(stubbed_environment, DUBBED)
    assert query['lang'] == ['en']
    assert 'tlang' not in query


def test_a_video_with_only_translations_into_english_keeps_them(stubbed_environment):
    translated = [_caption('de', tlang='en')]
    assert _fetched(stubbed_environment, translated)['lang'] == ['de']


# ------------------------------------------------------------ the fallback sequence


class _FakeYoutubeDL:
    """Records each re-run's options; raises whatever `outcomes` holds next."""

    outcomes: ClassVar[list] = []
    runs: ClassVar[list] = []

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def add_post_processor(self, _pp):
        pass

    def extract_info(self, _url, download=False):
        return {}

    def process_ie_result(self, _info, download=True):
        _FakeYoutubeDL.runs.append(self.options)
        outcome = _FakeYoutubeDL.outcomes.pop(0) if _FakeYoutubeDL.outcomes else None
        if outcome is not None:
            raise outcome


@pytest.fixture
def retries(monkeypatch):
    """Patch the re-run machinery; returns the recorded (fallback, subtitles) pairs."""

    def fake_create_ydl_options(_dto, **kwargs):
        return {
            'use_fallback_format': kwargs['use_fallback_format'],
            'with_subtitles': kwargs['with_subtitles'],
        }

    monkeypatch.setattr('tasks.downloads.create_ydl_options', fake_create_ydl_options)
    monkeypatch.setattr('tasks.downloads.YoutubeDL', _FakeYoutubeDL)
    _FakeYoutubeDL.outcomes = []
    _FakeYoutubeDL.runs = []

    def recorded():
        return [(r['use_fallback_format'], r['with_subtitles']) for r in _FakeYoutubeDL.runs]

    return recorded


def _run(primary_error, retry_outcomes=()):
    _FakeYoutubeDL.outcomes = list(retry_outcomes)
    ydl = MagicMock()
    ydl.process_ie_result.side_effect = primary_error
    _download_with_fallback(
        ydl,
        {'outtmpl': '/tmp/out.%(ext)s'},
        DownloadJobDTO(url=URL, generate_transcript=True),
        None,
        {},
        '',
        lambda _d: None,
        lambda _d: None,
        cookie_file=None,
    )


def test_caption_failure_reruns_without_subtitles(retries):
    _run(SUBTITLE_ERROR)
    assert retries() == [(False, False)]


def test_fallbacks_compose_format_then_captions(retries):
    _run(FORMAT_ERROR, [SUBTITLE_ERROR])
    assert retries() == [(True, True), (True, False)]


def test_fallbacks_compose_captions_then_format(retries):
    _run(SUBTITLE_ERROR, [FORMAT_ERROR])
    assert retries() == [(False, False), (True, False)]


def test_unrelated_error_is_not_retried(retries):
    error = DownloadError('ERROR: unable to download video data: HTTP Error 403')
    with pytest.raises(DownloadError) as raised:
        _run(error)
    assert raised.value is error
    assert retries() == []


def test_each_fallback_is_taken_once(retries):
    """A second caption failure after captions were dropped can't loop."""
    with pytest.raises(DownloadError):
        _run(SUBTITLE_ERROR, [SUBTITLE_ERROR])
    assert retries() == [(False, False)]


# ----------------------------------------------------------------- the job body


def test_download_succeeds_and_keeps_its_transcript(monkeypatch, retries):
    """The end state the bug denied: a COMPLETE download whose transcript still runs."""
    from tasks import downloads

    md = MediaDetailsDTO(id=1, url=URL, media_type=MediaType.VIDEO, title='T')
    dto = DownloadJobDTO(
        url=URL, media_type=MediaType.VIDEO, media_details=md, generate_transcript=True
    )

    primary = MagicMock()
    primary.__enter__.return_value = primary
    info = {'id': 'x', 'automatic_captions': {'en': list(DUBBED)}}
    primary.extract_info.return_value = info
    primary.process_ie_result.side_effect = SUBTITLE_ERROR

    # The body's own instance is constructed first; every later one is a fallback re-run.
    first = iter([primary])

    def youtube_dl(options):
        return next(first, None) or _FakeYoutubeDL(options)

    monkeypatch.setattr(downloads, 'YoutubeDL', youtube_dl)
    monkeypatch.setattr(downloads, '_resolve_live_media_details', lambda d: d)
    monkeypatch.setattr(downloads, '_determine_download_subdirectory', lambda _d: ('', False))
    monkeypatch.setattr(downloads, '_check_repeat_download', lambda _d: False)
    monkeypatch.setattr(downloads, 'cookie_session', lambda **_k: contextlib.nullcontext(None))
    monkeypatch.setattr(
        downloads, '_build_download_options', lambda *_a, **_k: {'outtmpl': 'primary'}
    )
    monkeypatch.setattr(downloads, '_check_file_exists_on_disk', lambda *_a: (None, False))
    monkeypatch.setattr(downloads, '_check_storage_quota', lambda _d: None)
    monkeypatch.setattr(downloads, '_rate_limit_sleep', lambda *_a: None)
    monkeypatch.setattr(downloads.md_repo, 'sync_get_media_details_by_id', lambda _id: None)
    monkeypatch.setattr(downloads.tr_repo, 'sync_update_one', lambda *_a, **_k: None)
    monkeypatch.setattr(downloads, 'is_video_ready_for_download', lambda _i: (True, ''))
    monkeypatch.setattr(downloads, '_post_download_update', lambda *_a: {'id': 1})

    ctx = JobContext(task_id='sub-fallback-download', user_id=1)
    result = downloads.run_download_job(ctx, dto.model_dump(mode='json'))

    assert result == {'id': 1}
    assert retries() == [(False, False)]
    assert ctx.skip_downstream is False, 'the transcript job must still run, on Whisper'
    assert info['automatic_captions']['en'] == [ORIGINAL], 'narrowed before the first fetch'
