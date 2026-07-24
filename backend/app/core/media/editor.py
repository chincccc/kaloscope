import asyncio
import contextlib
import uuid
from pathlib import Path

import aiofiles
from aiofiles import os as async_os
from lxml import etree
from sanic.request.form import File

from app.core.config import KaloscopeConfig
from app.core.constants import ENCODING
from app.core.exceptions import BadRequestException
from app.models.general import GlobalConfig
from app.models.media import MediaItem, NFOType

MAX_THUMBNAIL_SIZE = 10 * 1024 * 1024
FRAME_EXTRACTION_TIMEOUT = 30.0


def image_extension(data: bytes) -> str | None:
    """Detect a supported image extension from its file signature."""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return None


async def save_custom_thumbnail(file: File) -> str:
    """Validate and save an uploaded media thumbnail."""
    if not file.body or len(file.body) > MAX_THUMBNAIL_SIZE:
        raise BadRequestException
    extension = image_extension(file.body)
    if not extension:
        raise BadRequestException
    return await save_custom_thumbnail_bytes(file.body, extension)


async def save_custom_thumbnail_bytes(data: bytes, extension: str = ".jpg") -> str:
    """Save validated thumbnail bytes in the managed media image directory."""
    if not data or len(data) > MAX_THUMBNAIL_SIZE:
        raise BadRequestException

    directory = Path(KaloscopeConfig.get_workspace("images")) / "media"
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{extension}"
    async with aiofiles.open(directory / filename, "wb") as stream:
        await stream.write(data)
    return f"media/{filename}"


async def _ffmpeg_path() -> str:
    config = await GlobalConfig.get_or_none(key="ffmpeg.path")
    if config and isinstance(config.value, str) and Path(config.value).is_file():
        return config.value
    return "ffmpeg"


def frame_positions(duration: float, count: int) -> list[float]:
    """Return evenly distributed frame positions away from video boundaries."""
    if duration <= 0 or not 1 <= count <= 24:
        raise BadRequestException
    return [round(duration * (index + 1) / (count + 1), 3) for index in range(count)]


async def extract_video_frame(path: str, position: float) -> bytes:
    """Extract one bounded JPEG preview from a video with FFmpeg."""
    process = await asyncio.create_subprocess_exec(
        await _ffmpeg_path(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{position:.3f}",
        "-i",
        path,
        "-frames:v",
        "1",
        "-vf",
        "scale='min(1280,iw)':-2",
        "-q:v",
        "3",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(), timeout=FRAME_EXTRACTION_TIMEOUT
        )
    except TimeoutError:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        await process.communicate()
        raise BadRequestException from None
    if process.returncode != 0 or image_extension(stdout) != ".jpg":
        raise BadRequestException
    return stdout


async def save_video_frame(path: str, position: float) -> str:
    """Extract and persist one video frame as a custom thumbnail."""
    return await save_custom_thumbnail_bytes(
        await extract_video_frame(path, position), ".jpg"
    )


async def delete_custom_thumbnail(poster: str | None):
    """Delete a thumbnail managed by the media metadata editor."""
    if not poster or not poster.startswith("media/"):
        return
    if await MediaItem.filter(poster=poster).exists():
        return
    image_root = Path(KaloscopeConfig.get_workspace("images")).resolve()
    path = (image_root / poster).resolve()
    if path.parent != image_root / "media":
        return
    with contextlib.suppress(FileNotFoundError):
        await async_os.remove(path)


def _set_text(parent: etree._Element, tag: str, value: str | None):
    element = parent.find(tag)
    if value:
        if element is None:
            element = etree.SubElement(parent, tag)
        element.text = value
    elif element is not None:
        parent.remove(element)


async def edit_nfo(
    nfo_type: str,
    nfo_path: str,
    *,
    title: str,
    plot: str | None,
    poster: str | None,
):
    """Update description and poster without replacing other NFO metadata."""
    if nfo_type not in (NFOType.MOVIE, NFOType.TV_SHOW, NFOType.EPISODE):
        raise BadRequestException

    path = Path(nfo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        tree = etree.parse(path, parser=etree.XMLParser(recover=True))
        root = tree.getroot()
    else:
        root = etree.Element(str(nfo_type))
        tree = etree.ElementTree(root)
        _set_text(root, "title", title)

    _set_text(root, "plot", plot)
    art = root.find("art")
    if poster:
        if art is None:
            art = etree.SubElement(root, "art")
        _set_text(art, "poster", poster)
    elif art is not None:
        _set_text(art, "poster", None)
        if len(art) == 0 and not (art.text or "").strip():
            root.remove(art)

    document = etree.tostring(
        tree,
        encoding=ENCODING,
        xml_declaration=True,
        pretty_print=True,
    )
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    async with aiofiles.open(temporary, "wb") as stream:
        await stream.write(document)
    await async_os.replace(temporary, path)
