import hashlib
from urllib.parse import parse_qs, urlparse

CHANNEL_URL_PATTERNS = {
    'youtube.com': ['/@', '/channel/', '/c/', '/user/'],
}


def is_youtube_url(url: str) -> bool:
    """Check if a URL is a YouTube URL (used to conditionally apply YouTube-specific settings)."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or '').lower().replace('www.', '').replace('m.', '')
    return hostname in ('youtube.com', 'youtu.be', 'music.youtube.com')


def is_channel_or_feed_url(url: str) -> bool:
    """Check if a URL points to a YouTube channel or feed."""
    lower_url = url.lower()
    for domain, patterns in CHANNEL_URL_PATTERNS.items():
        if domain not in lower_url:
            continue
        parsed = urlparse(lower_url)
        hostname = (parsed.hostname or '').replace('www.', '').replace('m.', '')
        if hostname != domain:
            continue
        for pattern in patterns:
            if pattern in parsed.path:
                return True
    return False


def _extract_youtube_video_id(parsed, hostname: str) -> str | None:
    """Extract video ID from YouTube URL variants (watch, shorts, embed, youtu.be, live)."""
    if hostname == 'youtu.be':
        # youtu.be/VIDEO_ID
        path = parsed.path.strip('/')
        return path or None

    # youtube.com variants
    if hostname in ('youtube.com', 'music.youtube.com'):
        # /watch?v=VIDEO_ID
        params = parse_qs(parsed.query)
        if 'v' in params:
            return params['v'][0]

        # /shorts/VIDEO_ID, /embed/VIDEO_ID, /live/VIDEO_ID
        path_parts = parsed.path.strip('/').split('/')
        if len(path_parts) >= 2 and path_parts[0] in ('shorts', 'embed', 'live'):
            return path_parts[1]

    return None


def youtube_video_id(url: str) -> str | None:
    """The video id of a YouTube watch/shorts/embed/live/youtu.be URL, else None."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or '').lower().replace('www.', '').replace('m.', '')
    if hostname not in ('youtube.com', 'youtu.be', 'music.youtube.com'):
        return None
    return _extract_youtube_video_id(parsed, hostname)


def normalize_video_url(url: str) -> str:
    """Normalize a video URL. YouTube URLs are canonicalized; others pass through unchanged."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or '').lower().replace('www.', '').replace('m.', '')

    if hostname in ('youtube.com', 'youtu.be', 'music.youtube.com'):
        video_id = _extract_youtube_video_id(parsed, hostname)
        if video_id:
            return f'https://www.youtube.com/watch?v={video_id}'

    return url


def normalize_playlist_url(url: str) -> str | None:
    """Extract and normalize a YouTube playlist URL. Returns None for non-YouTube URLs."""
    if not is_youtube_url(url):
        return None
    parsed_url = urlparse(url)
    params = parse_qs(parsed_url.query)
    if 'list' in params:
        return f'https://www.youtube.com/playlist?list={params["list"][0]}'
    return None


def get_url_hash(url: str) -> str:
    """Generate a deterministic 11-char hash from a URL for use as a filename uniqueness suffix.

    The URL should already be normalized (via normalize_video_url) before reaching this point,
    so the same video always produces the same hash regardless of URL format.
    """
    return hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()[:11]
