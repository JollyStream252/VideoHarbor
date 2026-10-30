"""Best-effort XVideos creator/channel listing support for VideoHarbor.

The video streams themselves are always downloaded by yt-dlp. This module
only lists public creator uploads and does not bypass site restrictions.
"""
from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

try:
    from yt_dlp.utils import parse_duration as _parse_duration
except ImportError:
    _parse_duration = None

CHANNEL_KINDS = (
    'channels', 'profiles', 'models', 'amateur-channels',
    'model-channels', 'pornstar-channels',
)
_RESERVED = {
    'account', 'search', 'tag', 'tags', 'c', 'best', 'new', 'hits', 'top',
    'favorite', 'favorites', 'history', 'porn', 'pornstars', 'videos',
    'quickies', 'live', 'playlists', 'login', 'signup', 'settings', 'api',
    'embedframe', 'prof-video-click', 'premium', 'upload', 'subscriptions',
    'notifications', 'about', 'contact', 'terms', 'dmca', 'static',
    'gay', 'trans', 'shemale', 'straight',
}
_HOST = re.compile(r'(?:[a-z0-9-]+\.)*xvideos2?\.(?:com|es)', re.I)
_SLUG = re.compile(r'[a-z0-9_][a-z0-9_.-]*', re.I)
_BASE = 'https://www.xvideos.com'
_MAX_PAGES = 2000


def channel_from_url(url: str) -> tuple[str, str] | None:
    """Return (preferred_listing_kind, creator_slug), or None for non-channels.

    Bare creator slugs and the site's explicit creator-path forms are accepted.
    Video URLs, home pages, category pages, and unrelated hosts are excluded.
    """
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in ('https', 'http')
            or not _HOST.fullmatch(parsed.hostname or '')):
        return None
    segments = [urllib.parse.unquote(s) for s in parsed.path.split('/') if s]
    if not segments:
        return None
    if segments[0].lower() in CHANNEL_KINDS:
        if len(segments) < 2:
            return None
        slug = segments[1]
        kind = segments[0].lower()
    elif len(segments) == 1:
        slug = segments[0]
        kind = 'channels'
        if (slug.lower() in _RESERVED or slug.lower() in CHANNEL_KINDS
                or slug.lower().startswith(('video', 'embed', 'prof-video'))):
            return None
    else:
        return None
    if not _SLUG.fullmatch(slug) or slug in ('.', '..'):
        return None
    return kind, slug


def _fetch_listing(kind: str, slug: str, page: int) -> dict[str, Any] | None:
    """Retrieve a JSON listing page; a missing page returns None."""
    uri = f'{_BASE}/{kind}/{urllib.parse.quote(slug, safe="")}/videos/new/{page}'
    request = urllib.request.Request(uri, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; VideoHarbor/1.2)',
        'Accept': 'application/json, text/javascript, */*',
        'Referer': f'{_BASE}/{kind}/{urllib.parse.quote(slug, safe="")}',
    })
    try:
        with urllib.request.urlopen(request, timeout=22) as response:
            content = response.read(8_000_001)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None
    if len(content) > 8_000_000:
        raise ValueError('Channel listing response exceeds 8 MB.')
    try:
        parsed = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _clean_title(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = html.unescape(re.sub(r'<[^>]*>', '', value)).strip()
    return cleaned or fallback


def _video_url(entry: dict[str, Any]) -> str | None:
    eid = entry.get('eid')
    if eid and re.fullmatch(r'[a-zA-Z0-9]+', str(eid)):
        # The uploader's canonical URL can include a descriptive final slug.
        path = str(entry.get('u') or '').rstrip('/')
        suffix = path.rsplit('/', 1)[-1] if path else ''
        if suffix and suffix != str(eid) and re.fullmatch(r'[a-zA-Z0-9_-]+', suffix):
            return f'{_BASE}/video.{eid}/{suffix}'
        return f'{_BASE}/video.{eid}'
    vid = entry.get('id')
    if vid and str(vid).isdigit():
        return f'{_BASE}/video{vid}'
    return None


def _thumbnail(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = urllib.parse.urljoin(_BASE, value)
    return url if urllib.parse.urlsplit(url).scheme in ('http', 'https') else None


def iter_channel_videos(
    url: str,
    stop: Callable[[], bool] | None = None,
    fetch_page: Callable[[str, str, int], dict[str, Any] | None] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield channel videos with group, number, total, title and thumbnail.

    The upstream yt-dlp single-video extractor remains responsible for video
    formats and downloads. The listing endpoint is unofficial and may change.
    """
    parsed = channel_from_url(url)
    if parsed is None:
        raise ValueError('Not a supported XVideos creator URL.')
    preferred, slug = parsed
    stopped = stop or (lambda: False)
    fetch = fetch_page or _fetch_listing

    kinds = (preferred,) + tuple(k for k in CHANNEL_KINDS if k != preferred)
    kind = None
    first_page = None
    for candidate in kinds:
        if stopped():
            return
        page = fetch(candidate, slug, 0)
        if isinstance(page, dict) and isinstance(page.get('videos'), list):
            kind, first_page = candidate, page
            break
    if first_page is None:
        raise RuntimeError('Could not read this creator channel. The page may be unavailable or private.')

    group = _clean_title(
        next((item.get('pn') for item in first_page['videos']
              if isinstance(item, dict) and item.get('pn')), None),
        slug.replace('-', ' ').replace('_', ' ').title(),
    )
    try:
        declared_total = max(0, int(first_page.get('nb_videos') or 0))
    except (ValueError, TypeError):
        declared_total = 0
    try:
        page_size = max(1, int(first_page.get('nb_per_page') or 36))
    except (ValueError, TypeError):
        page_size = 36

    seen: set[str] = set()
    for page_number in range(_MAX_PAGES):
        if stopped():
            return
        listing = first_page if page_number == 0 else fetch(kind, slug, page_number)
        if not isinstance(listing, dict) or not isinstance(listing.get('videos'), list):
            if page_number:
                raise RuntimeError(f'Creator listing stopped at page {page_number + 1}; results may be incomplete.')
            return
        videos = listing['videos']
        if not videos:
            return
        new_count = 0
        for item in videos:
            if stopped():
                return
            if not isinstance(item, dict):
                continue
            video_url = _video_url(item)
            if not video_url or video_url in seen:
                continue
            seen.add(video_url)
            new_count += 1
            duration = None
            if _parse_duration is not None:
                duration = _parse_duration(item.get('d'))
            yield {
                'url': video_url,
                'title': _clean_title(item.get('tf') or item.get('t'), f'Video {len(seen)}'),
                'thumbnail': _thumbnail(item.get('il') or item.get('i')),
                'duration': duration,
                'group': group,
                'index': len(seen),
                'total': declared_total,
            }
        if not new_count or (declared_total and len(seen) >= declared_total) or len(videos) < page_size:
            return
    raise RuntimeError('Stopped after 2000 listing pages; results may be incomplete.')
