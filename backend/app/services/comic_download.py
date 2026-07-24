import asyncio
import contextlib
import re
from pathlib import Path
from time import monotonic
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

import aiofiles
import httpx
from sanic import Sanic
from sanic.log import logger
from tortoise import timezone

from app.core.exceptions import ErrorCode, KaloscopeException
from app.core.gallery_archive import MAX_ARCHIVE_IMAGE_SIZE
from app.core.network import NetworkTransport
from app.core.notifications import Notifications, NotificationTemplate
from app.core.resource_rename import normalize_resource_name
from app.models.download import ComicDownloadAdd, ComicDownloadTask, DownloadState
from app.models.gallery import Gallery
from app.services.base import BaseService
from app.services.gallery import IMAGE_EXTENSIONS, GalleryService

ARCHIVE_EXTENSIONS = frozenset({".cbz", ".zip"})
CONTENT_RANGE_TOTAL = re.compile(r"/([0-9]+)$")
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
PROGRESS_UPDATE_INTERVAL = 0.5
HTTP_DOWNLOAD_WORKERS = 3


def comic_archive_filename(add: ComicDownloadAdd) -> str:
    """Build a portable ZIP/CBZ file name from flow download metadata."""
    url_name = Path(unquote(urlsplit(add.url).path)).name
    supplied = add.filename.strip() if add.filename else ""
    candidate = supplied or add.title.strip() or url_name
    candidate_suffix = Path(candidate).suffix.lower()
    url_suffix = Path(url_name).suffix.lower()
    suffix = (
        candidate_suffix
        if candidate_suffix in ARCHIVE_EXTENSIONS
        else url_suffix
        if url_suffix in ARCHIVE_EXTENSIONS
        else ".cbz"
    )
    existing_suffix = (
        Path(candidate).suffix if candidate_suffix in ARCHIVE_EXTENSIONS else ""
    )
    stem = normalize_resource_name(candidate, existing_suffix)
    return f"{stem}{suffix}"


def validate_comic_archive(path: Path):
    """Verify that a completed archive contains at least one readable image."""
    try:
        with ZipFile(path) as archive:
            valid = any(
                not info.is_dir()
                and not info.flag_bits & 0x1
                and 0 < info.file_size <= MAX_ARCHIVE_IMAGE_SIZE
                and Path(info.filename).suffix.lower() in IMAGE_EXTENSIONS
                for info in archive.infolist()
            )
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise KaloscopeException(ErrorCode.BAD_REQUEST) from exc
    if not valid:
        raise KaloscopeException(ErrorCode.BAD_REQUEST)


def _safe_task_path(task: ComicDownloadTask, value: str) -> Path | None:
    root = Path(task.dir).resolve()
    path = Path(value).resolve()
    return path if path.parent == root else None


class ComicDownloadService(BaseService[ComicDownloadTask], model=ComicDownloadTask):
    """Persistent background downloader for ZIP/CBZ gallery resources."""

    _tasks: dict[int, asyncio.Task] = {}
    _semaphore = asyncio.Semaphore(HTTP_DOWNLOAD_WORKERS)
    _destination_lock = asyncio.Lock()

    @classmethod
    async def initialize(cls):
        for task in await ComicDownloadTask.filter(state=DownloadState.DOWNLOADING):
            cls._schedule(task.id)

    @classmethod
    async def shutdown(cls):
        running = list(cls._tasks.values())
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        cls._tasks.clear()

    @classmethod
    async def add(cls, add: ComicDownloadAdd) -> ComicDownloadTask:
        gallery = await Gallery.get_or_none(id=add.gallery_id)
        if gallery is None:
            raise KaloscopeException(ErrorCode.NOT_FOUND)
        root = Path(gallery.dir).resolve()
        if not root.is_dir():
            raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS)

        async with cls._destination_lock:
            name = comic_archive_filename(add)
            destination = root / name
            index = 2
            while (
                destination.exists()
                or await ComicDownloadTask.filter(
                    final_path=str(destination),
                    state__not=DownloadState.ERROR,
                ).exists()
            ):
                destination = root / f"{Path(name).stem} ({index}){Path(name).suffix}"
                index += 1
            temporary = (
                root / f".{destination.name}.{timezone.now().timestamp():.0f}.part"
            )
            task = await ComicDownloadTask.create(
                gallery_id=gallery.id,
                url=add.url,
                request_headers=add.headers or None,
                dir=str(root),
                name=destination.name,
                temp_path=str(temporary),
                final_path=str(destination),
                state=DownloadState.DOWNLOADING,
                completed_size=0,
                percentage=0,
            )
        cls._schedule(task.id)
        return task

    @classmethod
    def _schedule(cls, task_id: int):
        running = cls._tasks.get(task_id)
        if running is None or running.done():
            cls._tasks[task_id] = asyncio.create_task(cls._run(task_id))

    @classmethod
    async def _run(cls, task_id: int):
        try:
            async with cls._semaphore:
                await cls._download(task_id)
        except asyncio.CancelledError:
            await ComicDownloadTask.filter(id=task_id).update(dl_speed=0)
            raise
        except Exception as exc:
            logger.error("Comic download %s failed", task_id, exc_info=True)
            await ComicDownloadTask.filter(id=task_id).update(
                state=DownloadState.ERROR,
                error_msg=str(exc)[:4096] or exc.__class__.__name__,
                dl_speed=0,
            )
            task = await ComicDownloadTask.get_or_none(id=task_id)
            if task:
                await Notifications.send(
                    NotificationTemplate.DOWNLOAD_FAILED,
                    name=task.name,
                    error=str(exc),
                )
        finally:
            cls._tasks.pop(task_id, None)

    @classmethod
    async def _download(cls, task_id: int):
        task = await ComicDownloadTask.get(id=task_id)
        temporary = _safe_task_path(task, task.temp_path)
        destination = _safe_task_path(task, task.final_path)
        if temporary is None or destination is None:
            raise KaloscopeException(ErrorCode.BAD_REQUEST)
        temporary.parent.mkdir(parents=True, exist_ok=True)
        offset = temporary.stat().st_size if temporary.is_file() else 0
        headers = {**(task.request_headers or {})}
        headers.setdefault("Accept-Encoding", "identity")
        if offset:
            headers["Range"] = f"bytes={offset}-"

        app = Sanic.get_app()
        timeout = httpx.Timeout(connect=30, read=None, write=60, pool=30)
        async with (
            httpx.AsyncClient(
                follow_redirects=True,
                cookies=app.ctx.cookies,
                transport=NetworkTransport(http2=True),
                timeout=timeout,
            ) as client,
            client.stream("GET", task.url, headers=headers) as response,
        ):
            response.raise_for_status()
            resumed = offset > 0 and response.status_code == 206
            if not resumed:
                offset = 0
            content_length = int(response.headers.get("Content-Length") or 0)
            content_range = response.headers.get("Content-Range", "")
            match = CONTENT_RANGE_TOTAL.search(content_range)
            total = int(match.group(1)) if match else offset + content_length
            mode = "ab" if resumed else "wb"
            completed = offset
            started = monotonic()
            checkpoint = started
            checkpoint_size = completed
            async with aiofiles.open(temporary, mode) as output:
                async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK_SIZE):
                    await output.write(chunk)
                    completed += len(chunk)
                    now = monotonic()
                    if now - checkpoint >= PROGRESS_UPDATE_INTERVAL:
                        speed = int((completed - checkpoint_size) / (now - checkpoint))
                        percentage = completed / total * 100 if total else None
                        await ComicDownloadTask.filter(id=task_id).update(
                            state=DownloadState.DOWNLOADING,
                            error_msg=None,
                            dl_speed=speed,
                            percentage=percentage,
                            total_size=total or None,
                            completed_size=completed,
                        )
                        checkpoint = now
                        checkpoint_size = completed

        await asyncio.to_thread(validate_comic_archive, temporary)
        if destination.exists():
            raise KaloscopeException(ErrorCode.NAME_ALREADY_EXISTS)
        await asyncio.to_thread(temporary.replace, destination)
        await ComicDownloadTask.filter(id=task_id).update(
            state=DownloadState.COMPLETED,
            error_msg=None,
            dl_speed=0,
            percentage=100,
            total_size=completed,
            completed_size=completed,
            completed_at=timezone.now(),
        )
        if task.gallery_id:
            GalleryService.request_scan(task.gallery_id)
        await Notifications.send(
            NotificationTemplate.DOWNLOAD_COMPLETED, name=task.name
        )

    @classmethod
    async def pause(cls, task_id: int):
        task = await ComicDownloadTask.get(id=task_id)
        if task.state != DownloadState.DOWNLOADING:
            return
        running = cls._tasks.get(task_id)
        if running:
            running.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running
        await ComicDownloadTask.filter(id=task_id).update(
            state=DownloadState.PAUSED,
            dl_speed=0,
        )

    @classmethod
    async def start(cls, task_id: int):
        task = await ComicDownloadTask.get(id=task_id)
        if task.state not in {DownloadState.PAUSED, DownloadState.ERROR}:
            return
        await ComicDownloadTask.filter(id=task_id).update(
            state=DownloadState.DOWNLOADING,
            error_msg=None,
        )
        cls._schedule(task_id)

    @classmethod
    async def delete(cls, task_id: int, local: bool = False):
        task = await ComicDownloadTask.get(id=task_id)
        running = cls._tasks.get(task_id)
        if running:
            running.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running
        temporary = _safe_task_path(task, task.temp_path)
        if temporary:
            temporary.unlink(missing_ok=True)
        if local:
            destination = _safe_task_path(task, task.final_path)
            if destination:
                destination.unlink(missing_ok=True)
        await ComicDownloadTask.filter(id=task_id).delete()
