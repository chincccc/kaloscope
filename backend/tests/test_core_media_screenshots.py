import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.media import screenshots


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), (1, 1), (24, 24), (50, 24), (-3, 0), (True, 6), ("bad", 6)],
)
def test_screenshot_count_is_bounded(monkeypatch, value, expected):
    monkeypatch.setattr(
        screenshots.GlobalConfig,
        "get_or_none",
        AsyncMock(return_value=SimpleNamespace(value=value)),
    )
    assert asyncio.run(screenshots.screenshot_count()) == expected


def test_zero_screenshots_does_not_require_a_source(monkeypatch):
    monkeypatch.setattr(screenshots, "screenshot_count", AsyncMock(return_value=0))
    item = SimpleNamespace(id=7, path="/missing/video.mp4")

    assert asyncio.run(screenshots.request_screenshots(item)) == {
        "count": 0,
        "items": [],
        "pending": False,
        "error": False,
    }


def test_completed_cache_is_returned_without_enqueue(monkeypatch, tmp_path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    directory = tmp_path / "cache" / "7" / "fingerprint"
    directory.mkdir(parents=True)
    positions = [10.0, 20.0]
    (directory / "manifest.json").write_text(
        json.dumps({"positions": positions}), encoding="utf-8"
    )
    for index in range(2):
        (directory / f"{index:02d}.jpg").write_bytes(b"jpeg")

    monkeypatch.setattr(screenshots, "screenshot_count", AsyncMock(return_value=2))
    monkeypatch.setattr(screenshots, "source_fingerprint", lambda *_: "fingerprint")
    monkeypatch.setattr(screenshots, "cache_directory", lambda *_: directory)
    enqueue = AsyncMock()
    monkeypatch.setattr(screenshots, "_enqueue", enqueue)

    result = asyncio.run(
        screenshots.request_screenshots(SimpleNamespace(id=7, path=str(source)))
    )

    assert result["pending"] is False
    assert result["error"] is False
    assert [item["position"] for item in result["items"]] == positions
    assert result["items"][0]["url"].endswith("/7/screenshot/fingerprint/0")
    enqueue.assert_not_called()


def test_enqueue_deduplicates_an_active_job(monkeypatch, tmp_path):
    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()

        async def generate(*_):
            started.set()
            await release.wait()

        monkeypatch.setattr(screenshots, "_generate", generate)
        screenshots._tasks.clear()
        screenshots._failures.clear()

        assert screenshots._enqueue(3, tmp_path / "video.mp4", "abc", 6)
        await started.wait()
        task = screenshots._tasks["3:abc"]
        assert screenshots._enqueue(3, tmp_path / "video.mp4", "abc", 6)
        assert screenshots._tasks["3:abc"] is task

        release.set()
        await task
        await asyncio.sleep(0)
        assert "3:abc" not in screenshots._tasks

    asyncio.run(exercise())


def test_stale_cache_cleanup_stays_within_item_directory(tmp_path):
    item_root = tmp_path / "screenshots" / "5"
    current = item_root / "current"
    stale = item_root / "stale"
    sibling = item_root.parent / "other"
    current.mkdir(parents=True)
    stale.mkdir()
    sibling.mkdir()
    (stale / "00.jpg").write_bytes(b"old")

    screenshots._remove_stale_directories(item_root, current)

    assert current.is_dir()
    assert not stale.exists()
    assert sibling.is_dir()
