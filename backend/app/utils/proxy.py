"""Remote resource proxy utilities."""

import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from pydantic import BaseModel, Field

from app.core.exceptions import ForbiddenException

SENSITIVE_REQUEST_HEADERS = {
    "authorization",
    "cookie",
    "origin",
    "proxy",
    "proxy-authorization",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "sec-fetch-user",
}

PROXY_RESPONSE_HEADERS = [
    "accept-ranges",
    "cache-control",
    "content-encoding",
    "content-disposition",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "expires",
    "last-modified",
]

HLS_CONTENT_TYPES = {
    "application/mpegurl",
    "application/vnd.apple.mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
}
HLS_URI_RE = re.compile(r'URI=(?P<quote>["\'])(?P<url>.*?)(?P=quote)')


class RemoteProxy(BaseModel):
    """Request model for proxying a remote resource."""

    url: str = Field(min_length=1)
    store: bool = False
    referer: str | None = None
    ua: str | None = None


def _remote_media_proxy_url(url: str, referer: str | None, ua: str | None) -> str:
    """Build an application proxy URL for an HLS child resource."""
    params = {"url": url}
    if referer:
        params["referer"] = referer
    if ua:
        params["ua"] = ua
    filename = PurePosixPath(urlparse(url).path).name
    if not re.fullmatch(r"[A-Za-z0-9._-]+", filename or ""):
        filename = "stream"
    return f"/_api/media/proxy/{filename}?{urlencode(params)}"


def rewrite_hls_playlist(
    content: str,
    playlist_url: str,
    referer: str | None,
    ua: str | None,
) -> str:
    """Resolve and proxy every URI referenced by an HLS playlist."""

    def proxied(value: str) -> str:
        if value.startswith("data:"):
            return value
        return _remote_media_proxy_url(urljoin(playlist_url, value), referer, ua)

    lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            leading = line[: len(line) - len(line.lstrip())]
            line = f"{leading}{proxied(stripped)}"
        elif "URI=" in line:
            line = HLS_URI_RE.sub(
                lambda match: (
                    f"URI={match.group('quote')}"
                    f"{proxied(match.group('url'))}{match.group('quote')}"
                ),
                line,
            )
        lines.append(line)
    return "\n".join(lines) + ("\n" if content.endswith(("\n", "\r")) else "")


def remote_proxy_request(
    url: str,
    referer: str | None,
    user_agent: str | None = None,
    request_headers: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    """Build the upstream URL and headers for a remote proxy request.

    Args:
        url: The user-provided remote resource URL.
        referer: Optional referer override for the upstream request.
        user_agent: Optional user agent override for the upstream request.
        request_headers: Incoming request headers to selectively forward.

    Returns:
        The sanitized upstream URL and request headers.

    Raises:
        ForbiddenException: If the URL is not an absolute HTTP(S) URL.
    """
    parsed = urlparse(url)
    query_params: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        # strip app-only proxy params before forwarding the upstream request
        if key == "proxy":
            continue
        if key == "referer":
            referer = referer or value or None
            continue
        query_params.append((key, value))

    url = urlunparse(parsed._replace(query=urlencode(query_params)))
    parsed = urlparse(url)
    # only proxy absolute http urls to avoid local file or internal path access
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ForbiddenException

    # forward browser playback headers while dropping credentials and proxy-only headers
    dropped = SENSITIVE_REQUEST_HEADERS | {"host", "referer"}
    if user_agent:
        dropped |= {"user-agent"}
    headers = {
        str(key): str(value)
        for key, value in (request_headers or {}).items()
        if str(key).lower() not in dropped
    }
    parsed_referer = urlparse(referer or "")
    if parsed_referer.scheme in {"http", "https"} and parsed_referer.hostname:
        headers["Referer"] = referer
    if user_agent:
        headers["User-Agent"] = user_agent
    return url, headers
