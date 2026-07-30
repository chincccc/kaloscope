from urllib.parse import parse_qs, urlparse

from app.utils.proxy import remote_proxy_request, rewrite_hls_playlist


def _upstream_url(proxy_url: str) -> str:
    return parse_qs(urlparse(proxy_url).query)["url"][0]


def _proxy_path(proxy_url: str) -> str:
    return urlparse(proxy_url).path


def test_rewrite_hls_playlist_proxies_relative_resources():
    playlist = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="keys/key.bin"
#EXT-X-MAP:URI='../init.mp4'
#EXTINF:5,
segments/001.ts
#EXT-X-STREAM-INF:BANDWIDTH=1280000
720p/index.m3u8
"""

    rewritten = rewrite_hls_playlist(
        playlist,
        "https://cdn.example.com/live/master/index.m3u8",
        "https://example.com/watch/1",
        None,
    )
    lines = rewritten.splitlines()

    assert _upstream_url(lines[1].split('URI="', 1)[1][:-1]) == (
        "https://cdn.example.com/live/master/keys/key.bin"
    )
    assert _upstream_url(lines[2].split("URI='", 1)[1][:-1]) == (
        "https://cdn.example.com/live/init.mp4"
    )
    assert _upstream_url(lines[4]) == (
        "https://cdn.example.com/live/master/segments/001.ts"
    )
    assert _upstream_url(lines[6]) == (
        "https://cdn.example.com/live/master/720p/index.m3u8"
    )
    assert _proxy_path(lines[1].split('URI="', 1)[1][:-1]).endswith("/key.bin")
    assert _proxy_path(lines[2].split("URI='", 1)[1][:-1]).endswith("/init.mp4")
    assert _proxy_path(lines[4]).endswith("/001.ts")
    assert _proxy_path(lines[6]).endswith("/index.m3u8")
    assert "referer=https%3A%2F%2Fexample.com%2Fwatch%2F1" in lines[4]


def test_remote_media_request_drops_browser_cross_site_headers():
    _, headers = remote_proxy_request(
        "https://cdn.example.com/video.mp4",
        "https://example.com/watch/1",
        request_headers={
            "Origin": "http://localhost:8000",
            "Sec-Fetch-Dest": "video",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
            "Range": "bytes=0-1023",
            "User-Agent": "Browser UA",
        },
    )

    assert headers["Referer"] == "https://example.com/watch/1"
    assert headers["Range"] == "bytes=0-1023"
    assert headers["User-Agent"] == "Browser UA"
    assert "Host" not in headers
    assert "Origin" not in headers
    assert not any(key.lower().startswith("sec-fetch-") for key in headers)


def test_remote_media_request_ignores_non_url_referer():
    url, headers = remote_proxy_request(
        "https://cdn.example.com/video.mp4",
        "982699",
    )

    assert url == "https://cdn.example.com/video.mp4"
    assert "Referer" not in headers


def test_remote_media_request_does_not_invent_referer():
    _, headers = remote_proxy_request(
        "https://cdn.example.com/video.mp4",
        None,
    )

    assert "Referer" not in headers
