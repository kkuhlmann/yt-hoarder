"""Clips cut straight from YouTube, for items the app holds no file of."""

import contextlib
import subprocess
import threading
from http.cookiejar import Cookie
from types import SimpleNamespace

import pytest

from models import AppSettings
from orchestrator import JobCancelled
from tasks import clips

VIDEO_URL = 'https://rr1.googlevideo.com/videoplayback?itag=137'
AUDIO_URL = 'https://rr1.googlevideo.com/videoplayback?itag=140'
HEADERS = {'User-Agent': 'UA/1.0', 'Accept': '*/*'}


def _cookie(name: str, value: str) -> Cookie:
    return Cookie(
        0, name, value, None, False, '.googlevideo.com', True, True, '/', True, True,
        None, False, None, None, {},
    )  # fmt: skip


class FakeJar:
    def __init__(self, cookies=()):
        self.cookies = list(cookies)

    def get_cookies_for_url(self, _url):
        return self.cookies


def _dash_info() -> dict:
    return {
        'http_headers': {'User-Agent': 'fallback'},
        'requested_formats': [
            {'url': VIDEO_URL, 'http_headers': HEADERS},
            {'url': AUDIO_URL, 'http_headers': HEADERS},
        ],
    }


# ------------------------------------------------------------------- ffmpeg command


def test_local_clip_command_is_unchanged():
    cmd = clips._build_ffmpeg_clip_command([('/media/a.m4a', [])], '/out.mp3', 5.0, 10.0, False)

    assert cmd == [
        'ffmpeg', '-y', '-ss', '5.0', '-i', '/media/a.m4a', '-t', '10.0',
        '-acodec', 'libmp3lame', '-ab', '192k', '/out.mp3',
    ]  # fmt: skip


def test_separate_video_and_audio_streams_are_each_seeked_and_mapped():
    inputs = [(VIDEO_URL, ['-headers', 'h']), (AUDIO_URL, ['-headers', 'h'])]

    cmd = clips._build_ffmpeg_clip_command(inputs, '/out.mp4', 30.0, 8.0, True)

    assert cmd.count('-ss') == 2
    assert cmd[cmd.index(VIDEO_URL) - 3 : cmd.index(VIDEO_URL)] == ['-ss', '30.0', '-i']
    assert cmd[cmd.index(AUDIO_URL) - 3 : cmd.index(AUDIO_URL)] == ['-ss', '30.0', '-i']
    assert cmd[cmd.index('-map') :][:4] == ['-map', '0:v:0', '-map', '1:a:0']
    assert cmd[-1] == '/out.mp4'
    assert 'libx264' in cmd


def test_a_single_stream_is_not_mapped():
    cmd = clips._build_ffmpeg_clip_command([(AUDIO_URL, [])], '/out.mp3', 0.0, 3.0, False)

    assert '-map' not in cmd


# ------------------------------------------------------------------- stream inputs


def test_each_stream_carries_its_headers():
    inputs = clips._source_clip_inputs(_dash_info(), FakeJar())

    assert [url for url, _ in inputs] == [VIDEO_URL, AUDIO_URL]
    for _url, args in inputs:
        assert args == ['-headers', 'User-Agent: UA/1.0\r\nAccept: */*\r\n']


def test_a_single_format_falls_back_to_the_info_headers():
    info = {'url': AUDIO_URL, 'http_headers': {'User-Agent': 'fallback'}}

    assert clips._source_clip_inputs(info, FakeJar()) == [
        (AUDIO_URL, ['-headers', 'User-Agent: fallback\r\n'])
    ]


def test_session_cookies_reach_ffmpeg():
    jar = FakeJar([_cookie('VISITOR', 'abc'), _cookie('SID', 'xyz')])

    _url, args = clips._source_clip_inputs(_dash_info(), jar)[0]

    assert args[:2] == [
        '-cookies',
        'VISITOR=abc; path=/; domain=.googlevideo.com;\r\n'
        'SID=xyz; path=/; domain=.googlevideo.com;\r\n',
    ]


# ---------------------------------------------------------------------- job body


@pytest.fixture
def job_env(monkeypatch, tmp_path):
    """run_source_clip_job with yt-dlp, ffmpeg and the clip row replaced."""
    env = SimpleNamespace(ydl_options=None, info=_dash_info(), cmd=None, updates=[], ffmpeg=None)

    class FakeYoutubeDL:
        def __init__(self, options):
            env.ydl_options = options
            self.cookiejar = FakeJar()

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, url, download):
            assert download is False
            env.extracted_url = url
            return env.info

    def fake_ffmpeg(cmd, timeout, cancel_event=None):
        env.cmd = cmd
        if env.ffmpeg is not None:
            return env.ffmpeg(cmd)
        with open(cmd[-1], 'wb') as f:
            f.write(b'clip')
        return subprocess.CompletedProcess(cmd, 0, '', '')

    monkeypatch.setattr('tasks.clips.YoutubeDL', FakeYoutubeDL)
    monkeypatch.setattr('tasks.clips.run_ffmpeg_cancellable', fake_ffmpeg)
    monkeypatch.setattr('tasks.clips.cookie_session', lambda **_kw: contextlib.nullcontext(None))
    monkeypatch.setattr('ytdlp.options.settings_repo.sync_get_settings', lambda: AppSettings())
    monkeypatch.setattr(
        'tasks.clips.clips_repo.sync_update_clip', lambda cid, values: env.updates.append(values)
    )
    monkeypatch.setattr(clips.settings.storage, 'video_path', str(tmp_path / 'video'))
    monkeypatch.setattr(clips.settings.storage, 'audio_path', str(tmp_path / 'audio'))
    env.tmp_path = tmp_path
    return env


def _ctx():
    return SimpleNamespace(attempt=0, cancel_event=threading.Event())


def _payload(media_type: str) -> dict:
    return {
        'clip_id': 7,
        'media_details_id': 1,
        'start_time': 30.0,
        'end_time': 38.5,
        'media_type': media_type,
        'user_id': 1,
        'source_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    }


def test_video_clip_is_cut_from_both_streams(job_env):
    result = clips.run_source_clip_job(_ctx(), _payload('VIDEO'))

    expected = str(job_env.tmp_path / 'video' / 'clips' / 'clip_7.mp4')
    assert result == {'clip_id': 7, 'file_path': expected}
    assert job_env.updates == [{'file_path': expected, 'duration': 8.5}]
    assert job_env.extracted_url == 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
    assert VIDEO_URL in job_env.cmd
    assert AUDIO_URL in job_env.cmd
    assert 'bestvideo' in job_env.ydl_options['format']


def test_audio_clip_asks_yt_dlp_for_audio_only(job_env):
    job_env.info = {'url': AUDIO_URL, 'http_headers': HEADERS}

    result = clips.run_source_clip_job(_ctx(), _payload('AUDIO'))

    assert result['file_path'].endswith('/audio/clips/clip_7.mp3')
    assert job_env.ydl_options['format'].startswith('bestaudio')
    assert 'libmp3lame' in job_env.cmd


def test_a_source_not_yet_released_fails_without_running_ffmpeg(job_env):
    job_env.info = {**_dash_info(), 'live_status': 'is_upcoming'}

    with pytest.raises(RuntimeError, match='upcoming premiere'):
        clips.run_source_clip_job(_ctx(), _payload('VIDEO'))

    assert job_env.cmd is None
    assert job_env.updates == []


def test_a_failed_encode_leaves_no_partial_file(job_env):
    def fail(cmd):
        with open(cmd[-1], 'wb') as f:
            f.write(b'half')
        return subprocess.CompletedProcess(cmd, 1, '', 'Server returned 403 Forbidden')

    job_env.ffmpeg = fail

    with pytest.raises(RuntimeError, match='return code 1'):
        clips.run_source_clip_job(_ctx(), _payload('VIDEO'))

    assert not (job_env.tmp_path / 'video' / 'clips' / 'clip_7.mp4').exists()
    assert job_env.updates == []


class RecordingEvent:
    """A cancel event whose wait returns at once, recording how long it was asked to wait."""

    def __init__(self, cancelled: bool = False):
        self.cancelled = cancelled
        self.waits = []

    def wait(self, timeout):
        self.waits.append(timeout)
        return self.cancelled

    def is_set(self):
        return self.cancelled


def test_streams_are_fetched_only_once_youtube_serves_them(job_env, monkeypatch):
    """YouTube 403s a stream fetched before the pre-roll ad it holds it back for has run."""
    monkeypatch.setattr('tasks.clips.time.time', lambda: 1_000.0)
    job_env.info = {
        'requested_formats': [
            {'url': VIDEO_URL, 'available_at': 1_004},
            {'url': AUDIO_URL, 'available_at': 1_006},
        ]
    }
    event = RecordingEvent()

    clips.run_source_clip_job(SimpleNamespace(attempt=0, cancel_event=event), _payload('VIDEO'))

    assert event.waits == [6.0]
    assert job_env.cmd is not None


def test_no_wait_once_the_streams_are_served(job_env, monkeypatch):
    monkeypatch.setattr('tasks.clips.time.time', lambda: 1_000.0)
    job_env.info = {'url': AUDIO_URL, 'available_at': 990}
    event = RecordingEvent()

    clips.run_source_clip_job(SimpleNamespace(attempt=0, cancel_event=event), _payload('AUDIO'))

    assert event.waits == []


def test_a_cancel_during_the_wait_runs_no_ffmpeg(job_env, monkeypatch):
    monkeypatch.setattr('tasks.clips.time.time', lambda: 1_000.0)
    job_env.info = {'url': AUDIO_URL, 'available_at': 1_010}

    with pytest.raises(JobCancelled):
        clips.run_source_clip_job(
            SimpleNamespace(attempt=0, cancel_event=RecordingEvent(cancelled=True)),
            _payload('AUDIO'),
        )

    assert job_env.cmd is None
