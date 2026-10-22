"""Everything that asserts on the yt-dlp option dicts: audio selection, audio format,
and the app_settings throttling knobs."""

import pytest

from models import (
    DEFAULT_COOKIES_PLAYER_CLIENTS,
    DEFAULT_PLAYER_CLIENTS,
    AppSettings,
    AudioQuality,
)
from schemas import DownloadJobDTO
from ytdlp.info import _fetch_url_info
from ytdlp.options import (
    AUDIO_FORMATS,
    AUDIO_QUALITY_MAP,
    BGUTIL_POT_PATH,
    _get_pot_extractor_args,
    audio_quality_to_abr_cap,
    build_audio_selection,
    create_source_clip_options,
    create_ydl_options,
    get_stable_impersonate_target,
)

URL = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'


# ------------------------------------------------------------------- audio quality
#
# Audio quality is a downward re-encode ladder: higher tiers copy a native AAC
# source, sub-128k tiers re-encode DOWN to the exact target bitrate.


@pytest.mark.parametrize(
    'quality, expected',
    [
        (AudioQuality.BEST, None),
        (AudioQuality.K128, 128),
        (AudioQuality.K96, 96),
        (AudioQuality.K64, 64),
        (AudioQuality.K48, 48),
        # Raw string values, as they arrive from serialized job payloads.
        ('BEST', None),
        ('128K', 128),
        ('48K', 48),
        # Unknown / missing -> None (treated as Best).
        ('bogus', None),
        (None, None),
    ],
)
def test_audio_quality_to_abr_cap(quality, expected):
    assert audio_quality_to_abr_cap(quality) == expected


def test_audio_quality_map_covers_every_enum_value():
    for member in AudioQuality:
        assert member.value in AUDIO_QUALITY_MAP


def test_build_audio_selection_best_copies_source():
    # Best -> highest-quality source, copied (no re-encode target).
    assert build_audio_selection(None) == ('bestaudio/best', None)


def test_build_audio_selection_128k_copies_native_aac_with_headroom():
    # 128k copies the native ~128k AAC stream. YouTube reports it as ~129.5k, so the
    # selector needs headroom (round(128 * 1.15) == 147); no re-encode target.
    sel, pq = build_audio_selection(128)
    assert sel == 'bestaudio[ext=m4a][abr<=147]/bestaudio/best'
    assert pq is None


@pytest.mark.parametrize('cap', [96, 64, 48])
def test_build_audio_selection_low_tiers_reencode_from_opus(cap):
    # Sub-128k tiers prefer an opus source (forces a real re-encode) and carry the
    # exact target bitrate through to FFmpegExtractAudio.
    sel, pq = build_audio_selection(cap)
    assert sel == 'bestaudio[acodec=opus]/bestaudio/best'
    assert pq == cap


# -------------------------------------------------------------------- audio format


@pytest.fixture
def stubbed_environment(monkeypatch):
    monkeypatch.setattr('ytdlp.options.settings_repo.sync_get_settings', lambda: AppSettings())
    monkeypatch.setattr('ytdlp.options.os.makedirs', lambda *a, **k: None)


def _extract_audio_pp(options: dict) -> dict:
    pps = [p for p in options['postprocessors'] if p['key'] == 'FFmpegExtractAudio']
    assert len(pps) == 1
    return pps[0]


@pytest.mark.parametrize('fmt', AUDIO_FORMATS)
def test_audio_download_honors_requested_format(stubbed_environment, fmt):
    job = DownloadJobDTO(url='https://example.com/track', audio_only=True)
    options = create_ydl_options(job, fmt=fmt)
    assert _extract_audio_pp(options)['preferredcodec'] == fmt


def test_audio_download_defaults_to_m4a_for_video_formats(stubbed_environment):
    job = DownloadJobDTO(url='https://example.com/track', audio_only=True)
    options = create_ydl_options(job, fmt='mp4')
    assert _extract_audio_pp(options)['preferredcodec'] == 'm4a'


# ---------------------------------------------------------------------- throttling
#
# Two builders, deliberately asymmetric: `ratelimit` caps the media transfer and so
# belongs only to downloads, while `sleep_interval_requests` is paid during extraction
# and has to be set in both or it misses the bulk of this deployment's requests.


@pytest.fixture
def download_options(monkeypatch):
    monkeypatch.setattr('ytdlp.options.os.makedirs', lambda *a, **k: None)

    def build(
        *,
        use_fallback_format: bool = False,
        cookie_file: str | None = None,
        **setting_overrides,
    ) -> dict:
        monkeypatch.setattr(
            'ytdlp.options.settings_repo.sync_get_settings',
            lambda: AppSettings(**setting_overrides),
        )
        return create_ydl_options(
            DownloadJobDTO(url=URL),
            use_fallback_format=use_fallback_format,
            cookie_file=cookie_file,
        )

    return build


@pytest.fixture
def info_options(monkeypatch):
    """Capture the options dict `_fetch_url_info` hands to YoutubeDL."""
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, *_args, **_kwargs):
            return {}

    monkeypatch.setattr('ytdlp.info.YoutubeDL', FakeYoutubeDL)

    def build(**setting_overrides) -> dict:
        monkeypatch.setattr(
            'ytdlp.info.settings_repo.sync_get_settings',
            lambda: AppSettings(**setting_overrides),
        )
        # _fetch_url_info is TTL-cached on its arguments, which do not include settings.
        _fetch_url_info.cache.clear()
        captured.clear()
        _fetch_url_info(URL)
        return captured

    return build


def test_defaults_leave_downloads_unthrottled(download_options):
    options = download_options()
    assert 'ratelimit' not in options
    assert 'sleep_interval_requests' not in options


@pytest.mark.parametrize('kbps', [1, 512, 8192])
def test_rate_limit_is_converted_to_bytes_per_second(download_options, kbps):
    assert download_options(download_rate_limit_kbps=kbps)['ratelimit'] == kbps * 1024


def test_request_sleep_reaches_download_options(download_options):
    assert download_options(request_sleep_seconds=3)['sleep_interval_requests'] == 3


def test_fallback_format_path_stays_throttled(download_options):
    """The 'format not available' retry builds a fresh dict from scratch, so it would
    silently drop the cap if the keys were attached anywhere but create_ydl_options."""
    options = download_options(
        use_fallback_format=True, download_rate_limit_kbps=256, request_sleep_seconds=2
    )
    assert options['ratelimit'] == 256 * 1024
    assert options['sleep_interval_requests'] == 2


def test_defaults_leave_metadata_extraction_unthrottled(info_options):
    assert 'sleep_interval_requests' not in info_options()


def test_request_sleep_reaches_metadata_extraction(info_options):
    assert info_options(request_sleep_seconds=4)['sleep_interval_requests'] == 4


def test_rate_limit_never_reaches_metadata_extraction(info_options):
    assert 'ratelimit' not in info_options(download_rate_limit_kbps=512)


# ----------------------------------------------------------------- PO token provider
#
# yt-dlp derives a provider's extractor-arg key from its CLASS name
# (pot/_provider.py PROVIDER_KEY -> pot/_director.py 'youtubepot-{key.lower()}'),
# so BgUtilCliPTP reads 'youtubepot-bgutilcli:cli_path'. Unrecognised keys are
# silently dropped, which is why this needs a test rather than a log line.


@pytest.fixture
def pot_args(monkeypatch):
    def build(binary_present: bool) -> dict:
        monkeypatch.setattr('ytdlp.options.os.path.isfile', lambda _p: binary_present)
        return _get_pot_extractor_args()

    return build


def test_no_pot_args_without_the_binary(pot_args):
    assert pot_args(binary_present=False) == {}


def test_cli_provider_gets_the_key_it_actually_reads(pot_args):
    assert pot_args(binary_present=True)['youtubepot-bgutilcli'] == {'cli_path': [BGUTIL_POT_PATH]}


def test_script_key_is_retained_to_silence_the_http_provider(pot_args):
    """bgutil's HTTP provider probes this exact key to decide whether a refused
    connection to :4416 is expected. Dropping it warns on every extraction."""
    assert pot_args(binary_present=True)['youtubepot-bgutilscript'] == {
        'script_path': [BGUTIL_POT_PATH]
    }


# ------------------------------------------------------------------- cookie plumbing
#
# create_ydl_options takes an already-resolved path: cookie_session owns the mode
# decision and the temp copy, so this only asserts what the path selects.


def test_no_cookie_file_uses_the_anonymous_player_clients(download_options):
    options = download_options()
    assert 'cookiefile' not in options
    assert options['extractor_args']['youtube']['player_client'] == DEFAULT_PLAYER_CLIENTS


def test_a_cookie_file_selects_the_cookie_player_clients(download_options):
    options = download_options(cookie_file='/tmp/copy.txt')
    assert options['cookiefile'] == '/tmp/copy.txt'
    assert options['extractor_args']['youtube']['player_client'] == DEFAULT_COOKIES_PLAYER_CLIENTS


# ---------------------------------------------------------------------- impersonation


@pytest.fixture
def fake_targets(monkeypatch):
    targets = ('safari-18.0:ios-18.0', 'chrome-136:macos-15', 'chrome-133:macos-15')
    monkeypatch.setattr('ytdlp.options.get_available_impersonate_targets', lambda: targets)
    get_stable_impersonate_target.cache_clear()
    return targets


def test_stable_target_is_deterministic(fake_targets):
    assert get_stable_impersonate_target() == 'chrome-133:macos-15'


def test_stable_target_is_none_without_targets(monkeypatch):
    monkeypatch.setattr('ytdlp.options.get_available_impersonate_targets', lambda: ())
    get_stable_impersonate_target.cache_clear()
    assert get_stable_impersonate_target() is None


def test_anonymous_downloads_keep_a_random_fingerprint(download_options, monkeypatch):
    monkeypatch.setattr('ytdlp.options.get_random_impersonate_target', lambda: 'RANDOM-SENTINEL')
    assert download_options()['impersonate'] == 'RANDOM-SENTINEL'


def test_authenticated_downloads_pin_one_fingerprint(download_options, fake_targets, monkeypatch):
    """One account cycling 37 TLS fingerprints — each against a User-Agent
    impersonation does not replace — is the anomaly, not the cover."""
    monkeypatch.setattr('ytdlp.options.get_random_impersonate_target', lambda: 'RANDOM-SENTINEL')
    options = download_options(cookie_file='/tmp/copy.txt')
    assert options['impersonate'] == 'chrome-133:macos-15'


# ------------------------------------------------------------------ clips from source
#
# A clip of an item with no file only *resolves* streams through yt-dlp; ffmpeg does the
# transfer. So it presents itself exactly as a download does, but like metadata
# extraction it carries the request sleep and never the transfer cap.


@pytest.fixture
def source_clip_options(monkeypatch):
    def build(*, audio_only=False, cookie_file=None, **setting_overrides) -> dict:
        monkeypatch.setattr(
            'ytdlp.options.settings_repo.sync_get_settings',
            lambda: AppSettings(**setting_overrides),
        )
        return create_source_clip_options(URL, audio_only=audio_only, cookie_file=cookie_file)

    return build


def test_source_clip_uses_the_download_player_clients(source_clip_options):
    options = source_clip_options()
    assert options['extractor_args']['youtube']['player_client'] == DEFAULT_PLAYER_CLIENTS
    assert 'cookiefile' not in options


def test_source_clip_with_cookies_pins_one_fingerprint(source_clip_options, fake_targets):
    options = source_clip_options(cookie_file='/tmp/copy.txt')
    assert options['cookiefile'] == '/tmp/copy.txt'
    assert options['extractor_args']['youtube']['player_client'] == DEFAULT_COOKIES_PLAYER_CLIENTS
    assert options['impersonate'] == 'chrome-133:macos-15'


def test_source_clip_carries_the_request_sleep_but_no_transfer_cap(source_clip_options):
    options = source_clip_options(request_sleep_seconds=3, download_rate_limit_kbps=512)
    assert options['sleep_interval_requests'] == 3
    assert 'ratelimit' not in options


def test_source_clip_formats_skip_streams_ffmpeg_cannot_open(source_clip_options):
    for options in (source_clip_options(), source_clip_options(audio_only=True)):
        for alternative in options['format'].split('/'):
            assert '[protocol!=http_dash_segments]' in alternative


def test_source_clip_audio_never_asks_for_video(source_clip_options):
    assert 'video' not in source_clip_options(audio_only=True)['format']
