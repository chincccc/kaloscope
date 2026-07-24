import asyncio
import hashlib
import json
import shutil
import time
from pathlib import Path

import aiofiles
from aiofiles import os as async_os
from sanic.log import logger

from app.core.config import KaloscopeConfig
from app.core.exceptions import BadRequestException
from app.core.media.editor import extract_video_frame, frame_positions
from app.core.transcode import probe_media
from app.models.general import GlobalConfig
from app.models.media import MediaItem

SCREENSHOT_CONFIG_KEY = "media.screenshot_count"
DEFAULT_SCREENSHOT_COUNT = 6
MAX_SCREENSHOT_COUNT = 24
SCREENSHOT_WORKERS = 2
FAILURE_RETRY_SECONDS = 30

_tasks: dict[str, asyncio.Task] = {}
_failures: dict[str, float] = {}
_semaphore = asyncio.Semaphore(SCREENSHOT_WORKERS)


def screenshot_root() -> Path:
    return Path(KaloscopeConfig.get_workspace("images")) / "media" / "screenshots"


async def screenshot_count() -> int:
    config = await GlobalConfig.get_or_none(key=SCREENSHOT_CONFIG_KEY)
    value = config.value if config else DEFAULT_SCREENSHOT_COUNT
    if isinstance(value, bool):
        return DEFAULT_SCREENSHOT_COUNT
    try:
        return max(0, min(MAX_SCREENSHOT_COUNT, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_SCREENSHOT_COUNT


def source_fingerprint(path: Path, count: int) -> str:
    try:
        stat = path.stat()
    except OSError as exc:
        raise BadRequestException from exc
    source = f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{count}"
    return hashlib.sha256(source.encode()).hexdigest()[:16]


def cache_directory(item_id: int, fingerprint: str) -> Path:
    return screenshot_root() / str(item_id) / fingerprint


def cached_items(directory: Path, positions: list[float]) -> list[dict]:
    return [
        {
            "index": index,
            "position": position,
            "url": f"media/{directory.parent.name}/screenshot/{directory.name}/{index}",
        }
        for index, position in enumerate(positions)
        if (directory / f"{index:02d}.jpg").is_file()
    ]


async def _read_positions(directory: Path) -> list[float]:
    try:
        async with aiofiles.open(directory / "manifest.json", encoding="utf-8") as file:
            data = json.loads(await file.read())
        return [float(value) for value in data["positions"]]
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return []


def _remove_stale_directories(item_root: Path, current: Path):
    if not item_root.is_dir():
        return
    root = item_root.resolve()
    for child in item_root.iterdir():
        resolved = child.resolve()
        if child != current and resolved.parent == root and child.is_dir():
            shutil.rmtree(child, ignore_errors=True)


async def _generate(item_id: int, source: Path, fingerprint: str, count: int):
    key = f"{item_id}:{fingerprint}"
    directory = cache_directory(item_id, fingerprint)
    try:
        async with _semaphore:
            directory.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(
                _remove_stale_directories, directory.parent, directory
            )
            positions = await _read_positions(directory)
            if len(positions) != count:
                duration = (await probe_media(str(source))).duration or 0
                positions = frame_positions(duration, count)
                temporary = directory / "manifest.json.tmp"
                async with aiofiles.open(temporary, "w", encoding="utf-8") as file:
                    await file.write(json.dumps({"positions": positions}))
                await async_os.replace(temporary, directory / "manifest.json")

            for index, position in enumerate(positions):
                destination = directory / f"{index:02d}.jpg"
                if destination.is_file() and destination.stat().st_size > 0:
                    continue
                data = await extract_video_frame(str(source), position)
                temporary = destination.with_suffix(".jpg.tmp")
                async with aiofiles.open(temporary, "wb") as file:
                    await file.write(data)
                await async_os.replace(temporary, destination)
            _failures.pop(key, None)
    except asyncio.CancelledError:
        raise
    except Exception:
        _failures[key] = time.monotonic()
        logger.warning(
            "Failed to generate screenshots for media item %s", item_id, exc_info=True
        )


def _enqueue(item_id: int, source: Path, fingerprint: str, count: int) -> bool:
    key = f"{item_id}:{fingerprint}"
    task = _tasks.get(key)
    if task and not task.done():
        return True
    failed_at = _failures.get(key)
    if failed_at is not None and time.monotonic() - failed_at < FAILURE_RETRY_SECONDS:
        return False
    task = asyncio.create_task(_generate(item_id, source, fingerprint, count))
    _tasks[key] = task

    def discard(completed: asyncio.Task):
        if _tasks.get(key) is completed:
            _tasks.pop(key, None)

    task.add_done_callback(discard)
    return True


async def request_screenshots(item: MediaItem) -> dict:
    count = await screenshot_count()
    if count == 0:
        return {"count": 0, "items": [], "pending": False, "error": False}

    source = Path(item.path)
    if not source.is_file():
        raise BadRequestException
    fingerprint = source_fingerprint(source, count)
    directory = cache_directory(item.id, fingerprint)
    positions = await _read_positions(directory)
    items = cached_items(directory, positions)
    complete = len(positions) == count and len(items) == count
    pending = False if complete else _enqueue(item.id, source, fingerprint, count)
    key = f"{item.id}:{fingerprint}"
    return {
        "count": count,
        "items": items,
        "pending": pending,
        "error": not complete and not pending and key in _failures,
    }


async def current_screenshot_path(
    item: MediaItem, fingerprint: str, index: int
) -> Path | None:
    count = await screenshot_count()
    if count == 0 or not 0 <= index < count:
        return None
    source = Path(item.path)
    if not source.is_file() or source_fingerprint(source, count) != fingerprint:
        return None
    path = cache_directory(item.id, fingerprint) / f"{index:02d}.jpg"
    return path if path.is_file() else None


async def shutdown_screenshot_tasks():
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
