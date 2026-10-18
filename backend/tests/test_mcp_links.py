"""Links in MCP results: where they point and which origin the in-app ones use."""

from urllib.parse import parse_qs, urlsplit

import pytest

from mcp_server.links import app_url, format_timestamp, resolve_base_url, source_url


def test_public_url_wins_over_the_request():
    base = resolve_base_url('http', 'nas:8000', 'https://hoarder.example.com', dev_frontend=False)

    assert base == 'https://hoarder.example.com'


def test_base_comes_from_scheme_and_host_without_public_url():
    assert resolve_base_url('https', 'hoarder.lan', '', dev_frontend=False) == 'https://hoarder.lan'
    assert resolve_base_url('http', 'nas:8000', '', dev_frontend=False) == 'http://nas:8000'


def test_dev_points_at_the_next_dev_server_not_the_api():
    assert resolve_base_url('http', 'nas:8000', '', dev_frontend=True) == 'http://nas:3000'
    assert resolve_base_url('http', '[::1]:8000', '', dev_frontend=True) == 'http://[::1]:3000'


@pytest.mark.parametrize('host', [None, '', 'evil.com/path', 'a b', 'x"><script>'])
def test_a_malformed_host_falls_back_to_localhost(host):
    assert resolve_base_url('http', host, '', dev_frontend=False) == 'http://localhost'


def test_an_unexpected_scheme_is_not_echoed():
    assert resolve_base_url('javascript', 'nas', '', dev_frontend=False) == 'http://nas'


@pytest.mark.parametrize(
    ('url', 'expected_host'),
    [
        ('https://www.youtube.com/watch?v=abc', 'www.youtube.com'),
        ('https://youtu.be/abc', 'youtu.be'),
        ('https://www.youtube.com/watch?v=abc&t=10', 'www.youtube.com'),
    ],
)
def test_source_url_seeks_with_t(url, expected_host):
    parts = urlsplit(source_url(url, 754.9))

    assert parts.netloc == expected_host
    assert parse_qs(parts.query)['t'] == ['754']


def test_source_url_keeps_other_params():
    query = parse_qs(urlsplit(source_url('https://www.youtube.com/watch?v=abc', 5)).query)

    assert query == {'v': ['abc'], 't': ['5']}


@pytest.mark.parametrize('url', [None, '', 'file:///etc/passwd', 'javascript:alert(1)'])
def test_source_url_refuses_what_is_not_a_web_page(url):
    assert source_url(url, 5) is None


def test_app_url_is_the_root_page_with_media_and_t():
    assert app_url('http://nas:8000', 12, 754.6) == 'http://nas:8000/?media=12&t=754'


@pytest.mark.parametrize(('seconds', 'expected'), [(0, '0:00'), (75.9, '1:15'), (3725, '1:02:05')])
def test_format_timestamp(seconds, expected):
    assert format_timestamp(seconds) == expected
