import asyncio
import contextlib
import hashlib
from pathlib import Path
from weakref import WeakValueDictionary

from aiofiles import os as async_os
from sanic.log import logger

from app.core.config import KaloscopeConfig
from app.core.exceptions import ErrorCode, KaloscopeException
from app.models.general import GlobalConfig

THUMBNAIL_MAX_SIZE = 640
THUMBNAIL_TIMEOUT = 30.0
THUMBNAIL_WORKERS = 4

_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_semaphore = asyncio.Semaphore(THUMBNAIL_WORKERS)


def gallery_thumbnail_fingerprint(path: Path) -> str:
    """Return a cache key that changes whenever the source image changes."""
    stat = path.stat()
    source = f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha256(source.encode()).hexdigest()[:16]


def gallery_thumbnail_path(item_id: int, fingerprint: str) -> Path:
    root = Path(KaloscopeConfig.get_workspace("images")) / "gallery" / "covers"
    return root / str(item_id) / f"{fingerprint}.webp"


async def _ffmpeg_path() -> str:
    config = await GlobalConfig.get_or_none(key="ffmpeg.path")
    if config and isinstance(config.value, str) and Path(config.value).is_file():
        return config.value
    return "ffmpeg"


def _remove_stale_covers(directory: Path, current: Path):
    if not directory.is_dir():
        return
    for child in directory.iterdir():
        if child != current and child.is_file():
            child.unlink(missing_ok=True)


async def ensure_gallery_thumbnail(item_id: int, source: Path) -> Path:
    """Generate and cache a bounded WebP cover for a gallery item."""
    if not source.is_file():
        raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS)
    fingerprint = gallery_thumbnail_fingerprint(source)
    destination = gallery_thumbnail_path(item_id, fingerprint)
    if destination.is_file() and destination.stat().st_size > 0:
        return destination

    key = f"{item_id}:{fingerprint}"
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        if destination.is_file() and destination.stat().st_size > 0:
            return destination
        async with _semaphore:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".webp.tmp")
            process = await asyncio.create_subprocess_exec(
                await _ffmpeg_path(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                (
                    f"scale='min({THUMBNAIL_MAX_SIZE},iw)':"
                    f"'min({THUMBNAIL_MAX_SIZE},ih)':"
                    "force_original_aspect_ratio=decrease"
                ),
                "-c:v",
                "libwebp",
                "-q:v",
                "72",
                "-f",
                "webp",
                "-y",
                str(temporary),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=THUMBNAIL_TIMEOUT
                )
            except TimeoutError:
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                await process.communicate()
                temporary.unlink(missing_ok=True)
                raise KaloscopeException(ErrorCode.INTERNAL_SERVER_ERROR) from None

            if (
                process.returncode != 0
                or not temporary.is_file()
                or temporary.stat().st_size == 0
            ):
                temporary.unlink(missing_ok=True)
                logger.warning(
                    "Failed to generate gallery cover for %s: %s",
                    source,
                    stderr.decode(errors="replace").strip(),
                )
                raise KaloscopeException(ErrorCode.INTERNAL_SERVER_ERROR)

            await async_os.replace(temporary, destination)
            await asyncio.to_thread(
                _remove_stale_covers, destination.parent, destination
            )

    return destination
