"""Links in MCP results: back to the source at a timestamp, and into ytdl-hoarder's player."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# The dev UI is served by Next on :3000 while the API (and so the MCP endpoint) is on
# :8000, so a link built from the MCP request's own address would open the API. Mirrors
# DEV_API_PORT in frontend/app/lib/api.ts, from the other side.
DEV_FRONTEND_PORT = 3000

# Host is caller-supplied. Anything outside this set is not a hostname we should put
# into a link, so it falls back to localhost rather than being echoed.
_HOST_RE = re.compile(r'^[A-Za-z0-9.\-]+(:\d{1,5})?$|^\[[0-9A-Fa-f:.]+\](:\d{1,5})?$')


def resolve_base_url(
    scheme: str, host_header: str | None, public_url: str, dev_frontend: bool
) -> str:
    """The origin in-app links should use.

    `public_url` wins when set. Otherwise the agent's own view of the server is the best
    guess for where the user's browser reaches it too: uvicorn has already rewritten the
    scheme from X-Forwarded-Proto for a trusted proxy, but the Host header is taken as
    sent, so a proxy that rewrites Host needs `public_url`.
    """
    if public_url:
        return public_url
    scheme = scheme if scheme in ('http', 'https') else 'http'
    host = (host_header or '').strip()
    if not _HOST_RE.match(host):
        host = 'localhost'
    if dev_frontend:
        hostname = urlsplit(f'//{host}').hostname or 'localhost'
        if ':' in hostname:
            hostname = f'[{hostname}]'
        host = f'{hostname}:{DEV_FRONTEND_PORT}'
    return f'{scheme}://{host}'


def source_url(url: str | None, seconds: float) -> str | None:
    """The media's original page, seeking to `seconds` with a `t` query parameter.

    Same shape the frontend builds for deleted media in TranscriptSegmentTable, which
    YouTube's watch and youtu.be URLs both honour.
    """
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https'):
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != 't']
    query.append(('t', str(int(seconds))))
    return urlunsplit(parts._replace(query=urlencode(query)))


def app_url(base_url: str, media_id: int, seconds: float) -> str:
    return f'{base_url}/?{urlencode({"media": media_id, "t": int(seconds)})}'


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f'{hours}:{minutes:02d}:{secs:02d}'
    return f'{minutes}:{secs:02d}'
