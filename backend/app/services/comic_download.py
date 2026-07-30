import asyncio
import contextlib
import re
from html import unescape as html_unescape
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qsl, unquote, urlsplit
from zipfile import BadZipFile, ZipFile

import aiofiles
import httpx
from sanic import Sanic
from sanic.log import logger
from tortoise import timezone

from app.core.exceptions import ErrorCode, KaloscopeException
from app.core.gallery_archive import MAX_ARCHIVE_IMAGE_SIZE
from app.core.network import NetworkTransport, resolve_proxy
from app.core.notifications import Notifications, NotificationTemplate
from app.core.transcode.transcoder import _ffmpeg
from app.models.download import (
    BuiltinDownloadType,
    ComicDownloadAdd,
    ComicDownloadTask,
    DownloadState,
)
from app.models.gallery import Gallery
from app.models.media import MediaLib
from app.services.base import BaseService
from app.services.gallery import IMAGE_EXTENSIONS, GalleryService
from app.utils.proxy import remote_proxy_request

ARCHIVE_EXTENSIONS = frozenset({".cbz", ".zip"})
VIDEO_EXTENSIONS = frozenset(
    {
        ".avi",
        ".flv",
        ".m2ts",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".ts",
        ".webm",
        ".wmv",
    }
)
DOWNLOAD_FILENAME_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_DOWNLOAD_STEM_BYTES = 220
CONTENT_RANGE_TOTAL = re.compile(r"/([0-9]+)$")
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
PROGRESS_UPDATE_INTERVAL = 0.5
HTTP_DOWNLOAD_WORKERS = 3
MAX_COVER_SIZE = 32 * 1024 * 1024
COVER_STEMS = frozenset({"cover", "folder", "front", "000000_cover"})
COVER_MIME_SUFFIXES = {
    "image/avif": ".avif",
    "image/bmp": ".bmp",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


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
    stem = download_resource_stem(candidate, existing_suffix)
    return f"{stem}{suffix}"


def download_resource_stem(value: str, suffix: str = "") -> str:
    """Sanitize untrusted workflow titles into portable download names."""
    name = html_unescape(value).strip()
    if suffix and name.casefold().endswith(suffix.casefold()):
        name = name[: -len(suffix)].rstrip()
    name = DOWNLOAD_FILENAME_UNSAFE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name or name in {".", ".."}:
        name = "download"
    while len(name.encode("utf-8")) > MAX_DOWNLOAD_STEM_BYTES:
        name = name[:-1]
    return name.rstrip(" .") or "download"


def video_filename(add: ComicDownloadAdd) -> str:
    """Build a portable video filename while preserving a useful URL suffix."""
    url_name = Path(unquote(urlsplit(add.url).path)).name
    supplied = add.filename.strip() if add.filename else ""
    candidate = supplied or add.title.strip() or url_name or "video"
    candidate_suffix = Path(candidate).suffix
    url_suffix = Path(url_name).suffix
    suffix = (
        candidate_suffix
        if candidate_suffix.lower() in VIDEO_EXTENSIONS
        else url_suffix
        if url_suffix.lower() in VIDEO_EXTENSIONS
        else ".mp4"
    )
    if add.type == BuiltinDownloadType.HLS:
        suffix = ".mp4"
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,8}", suffix):
        suffix = ".mp4"
    existing_suffix = (
        Path(candidate).suffix
        if candidate_suffix.lower() in VIDEO_EXTENSIONS
        else ""
    )
    stem = download_resource_stem(candidate, existing_suffix)
    return f"{stem}{suffix.lower()}"


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


def enrich_comic_archive(
    path: Path,
    cover: tuple[bytes, str] | None = None,
):
    """Add an optional leading cover image without replacing an existing cover."""
    with ZipFile(path, "a") as archive:
        members = [
            Path(info.filename.replace("\\", "/")) for info in archive.infolist()
        ]
        has_cover = any(
            member.suffix.lower() in IMAGE_EXTENSIONS
            and member.stem.casefold() in COVER_STEMS
            for member in members
        )
        if cover and not has_cover:
            content, suffix = cover
            archive.writestr(f"000000_cover{suffix}", content)


def _cover_suffix(content: bytes, content_type: str | None, url: str) -> str | None:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if suffix := COVER_MIME_SUFFIXES.get(mime):
        return suffix
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if content.startswith(b"BM"):
        return ".bmp"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    if len(content) >= 12 and content[4:12] in {b"ftypavif", b"ftypavis"}:
        return ".avif"
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix in IMAGE_EXTENSIONS else None


def _cover_request(
    value: str,
    request_headers: dict[str, str] | None,
) -> tuple[str, dict[str, str]]:
    parsed = urlsplit(value)
    referer = None
    if parsed.path.endswith("/_api/image/proxy"):
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        value = params.get("url", "")
        referer = params.get("referer") or None
    if referer is None:
        referer = next(
            (
                header_value
                for key, header_value in (request_headers or {}).items()
                if key.casefold() == "referer"
            ),
            None,
        )
    return remote_proxy_request(value, referer, request_headers=request_headers)


def _safe_task_path(task: ComicDownloadTask, value: str) -> Path | None:
    root = Path(task.dir).resolve()
    path = Path(value).resolve()
    return path if path.parent == root else None


class ComicDownloadService(BaseService[ComicDownloadTask], model=ComicDownloadTask):
    """Persistent built-in downloader for comics, direct videos and HLS."""

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
        gallery = None
        media_lib = None
        if add.type == BuiltinDownloadType.COMIC:
            gallery = await Gallery.get_or_none(id=add.gallery_id)
            root = Path(gallery.dir).resolve() if gallery else None
        else:
            media_lib = await MediaLib.get_or_none(id=add.media_lib_id)
            root = Path(media_lib.dir).resolve() if media_lib else None
        if root is None:
            raise KaloscopeException(ErrorCode.NOT_FOUND)
        if not root.is_dir():
            raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS)

        async with cls._destination_lock:
            name = (
                comic_archive_filename(add)
                if add.type == BuiltinDownloadType.COMIC
                else video_filename(add)
            )
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
                download_type=add.type,
                gallery_id=gallery.id if gallery else None,
                media_lib_id=media_lib.id if media_lib else None,
                url=add.url,
                request_headers=add.headers or None,
                title=add.title.strip(),
                cover=add.cover.strip() if add.cover else None,
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
        if task.download_type == BuiltinDownloadType.HLS:
            completed = await cls._download_hls(task, temporary)
        else:
            completed = await cls._download_http(task, temporary)

        if task.download_type == BuiltinDownloadType.COMIC:
            await asyncio.to_thread(validate_comic_archive, temporary)
            cover = await cls._download_cover(task)
            await asyncio.to_thread(enrich_comic_archive, temporary, cover)
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
    async def _download_http(cls, task: ComicDownloadTask, temporary: Path) -> int:
        if task.download_type == BuiltinDownloadType.VIDEO:
            return await cls._download_video_http(task, temporary)
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
                        await ComicDownloadTask.filter(id=task.id).update(
                            state=DownloadState.DOWNLOADING,
                            error_msg=None,
                            dl_speed=speed,
                            percentage=percentage,
                            total_size=total or None,
                            completed_size=completed,
                        )
                        checkpoint = now
                        checkpoint_size = completed
        return completed

    @classmethod
    async def _download_video_http(
        cls, task: ComicDownloadTask, temporary: Path
    ) -> int:
        """Download protected video links with the shared browser session."""
        offset = temporary.stat().st_size if temporary.is_file() else 0
        headers = {
            key: value
            for key, value in (task.request_headers or {}).items()
            if key.casefold()
            not in {
                "user-agent",
                "sec-ch-ua",
                "sec-ch-ua-mobile",
                "sec-ch-ua-platform",
            }
        }
        headers.setdefault("Accept-Encoding", "identity")
        if offset:
            headers["Range"] = f"bytes={offset}-"

        app = Sanic.get_app()
        response = await app.ctx.curl_cffi.get(
            task.url,
            headers=list(headers.items()),
            proxy=await resolve_proxy(task.url),
            impersonate="chrome",
            stream=True,
        )
        try:
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
            checkpoint = monotonic()
            checkpoint_size = completed
            async with aiofiles.open(temporary, mode) as output:
                async for chunk in response.aiter_content():
                    await output.write(chunk)
                    completed += len(chunk)
                    now = monotonic()
                    if now - checkpoint >= PROGRESS_UPDATE_INTERVAL:
                        speed = int(
                            (completed - checkpoint_size) / (now - checkpoint)
                        )
                        percentage = completed / total * 100 if total else None
                        await ComicDownloadTask.filter(id=task.id).update(
                            state=DownloadState.DOWNLOADING,
                            error_msg=None,
                            dl_speed=speed,
                            percentage=percentage,
                            total_size=total or None,
                            completed_size=completed,
                        )
                        checkpoint = now
                        checkpoint_size = completed
            return completed
        finally:
            await response.aclose()

    @classmethod
    async def _download_hls(cls, task: ComicDownloadTask, temporary: Path) -> int:
        """Merge HLS streams with ffmpeg; a resumed task restarts cleanly."""
        temporary.unlink(missing_ok=True)
        headers = "".join(
            f"{key}: {value}\r\n" for key, value in (task.request_headers or {}).items()
        )
        args = [await _ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
        if (proxy := await resolve_proxy(task.url)) and proxy.startswith(
            ("http://", "https://")
        ):
            args.extend(["-http_proxy", proxy])
        if headers:
            args.extend(["-headers", headers])
        args.extend(
            [
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_delay_max",
                "5",
                "-rw_timeout",
                "30000000",
                "-allowed_extensions",
                "ALL",
                "-allowed_segment_extensions",
                "ALL",
                "-extension_picky",
                "0",
                "-i",
                task.url,
                "-map",
                "0:v?",
                "-map",
                "0:a?",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(temporary),
            ]
        )
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        stderr_task = (
            asyncio.create_task(process.stderr.read()) if process.stderr else None
        )
        started = monotonic()
        last_size = 0
        last_update = started
        try:
            while process.returncode is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        process.wait(), timeout=PROGRESS_UPDATE_INTERVAL
                    )
                size = temporary.stat().st_size if temporary.is_file() else 0
                now = monotonic()
                speed = int((size - last_size) / max(now - last_update, 0.001))
                await ComicDownloadTask.filter(id=task.id).update(
                    state=DownloadState.DOWNLOADING,
                    error_msg=None,
                    dl_speed=max(speed, 0),
                    percentage=None,
                    total_size=None,
                    completed_size=size,
                )
                last_size = size
                last_update = now
        except asyncio.CancelledError:
            if process.returncode is None:
                process.terminate()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=5)
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            if stderr_task:
                await stderr_task
            raise
        stderr = (await stderr_task).decode(errors="replace") if stderr_task else ""
        if process.returncode != 0:
            temporary.unlink(missing_ok=True)
            detail = stderr[-4096:].strip()
            raise RuntimeError(detail or f"ffmpeg exited with {process.returncode}")
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("ffmpeg produced an empty video")
        return temporary.stat().st_size

    @classmethod
    async def _download_cover(
        cls,
        task: ComicDownloadTask,
    ) -> tuple[bytes, str] | None:
        if not task.cover:
            return None
        try:
            url, headers = _cover_request(task.cover, task.request_headers)
            headers["Accept"] = "image/avif,image/webp,image/*,*/*;q=0.8"
            app = Sanic.get_app()
            timeout = httpx.Timeout(connect=30, read=60, write=30, pool=30)
            async with (
                httpx.AsyncClient(
                    follow_redirects=True,
                    cookies=app.ctx.cookies,
                    transport=NetworkTransport(http2=True),
                    timeout=timeout,
                ) as client,
                client.stream("GET", url, headers=headers) as response,
            ):
                response.raise_for_status()
                length = int(response.headers.get("Content-Length") or 0)
                if length > MAX_COVER_SIZE:
                    raise ValueError("comic cover is too large")
                content = bytearray()
                async for chunk in response.aiter_bytes(256 * 1024):
                    content.extend(chunk)
                    if len(content) > MAX_COVER_SIZE:
                        raise ValueError("comic cover is too large")
                suffix = _cover_suffix(
                    bytes(content),
                    response.headers.get("Content-Type"),
                    str(response.url),
                )
                if not content or suffix is None:
                    raise ValueError("comic cover is not a supported image")
                return bytes(content), suffix
        except Exception:
            logger.warning(
                "Failed to preserve cover for comic download %s",
                task.id,
                exc_info=True,
            )
            return None

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
