import asyncio
import hashlib
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote, unquote
from weakref import WeakValueDictionary
from zipfile import BadZipFile, ZipFile

from sanic.log import logger

from app.core.config import KaloscopeConfig
from app.core.exceptions import ErrorCode, KaloscopeException

ARCHIVE_EXTENSIONS = frozenset({".cbz", ".zip"})
ARCHIVE_PATH_PREFIX = "zip:"
MAX_ARCHIVE_IMAGE_SIZE = 512 * 1024 * 1024
ARCHIVE_READ_WORKERS = 4

_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_semaphore = asyncio.Semaphore(ARCHIVE_READ_WORKERS)


def encode_archive_member(archive: Path, member: str) -> str:
    """Encode an archive and member name into a stable GalleryItem path."""
    archive_value = quote(str(archive.resolve()), safe="")
    member_value = quote(member, safe="")
    return f"{ARCHIVE_PATH_PREFIX}{archive_value}!{member_value}"


def decode_archive_member(value: str) -> tuple[Path, str] | None:
    """Decode a GalleryItem path, or return None for a regular image path."""
    if not value.startswith(ARCHIVE_PATH_PREFIX):
        return None
    encoded = value[len(ARCHIVE_PATH_PREFIX) :]
    try:
        archive, member = encoded.split("!", 1)
    except ValueError:
        return None
    if not archive or not member:
        return None
    return Path(unquote(archive)), unquote(member)


def _member_fingerprint(archive: Path, member: str) -> str:
    stat = archive.stat()
    value = f"{archive.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{member}"
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def _member_cache_path(value: str, archive: Path, member: str) -> Path:
    source_key = hashlib.sha256(value.encode()).hexdigest()[:20]
    fingerprint = _member_fingerprint(archive, member)
    suffix = Path(member).suffix.lower()
    root = Path(KaloscopeConfig.get_workspace("images")) / "gallery" / "archive"
    return root / source_key / f"{fingerprint}{suffix}"


def _remove_stale_members(directory: Path, current: Path):
    if not directory.is_dir():
        return
    for child in directory.iterdir():
        if child != current and child.is_file() and not child.name.startswith("."):
            child.unlink(missing_ok=True)


def _extract_member(archive: Path, member: str, destination: Path):
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    try:
        with ZipFile(archive) as zip_file:
            info = zip_file.getinfo(member)
            if info.is_dir() or info.flag_bits & 0x1:
                raise KaloscopeException(ErrorCode.BAD_REQUEST)
            if info.file_size > MAX_ARCHIVE_IMAGE_SIZE:
                raise KaloscopeException(ErrorCode.BAD_REQUEST)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zip_file.open(info) as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            if temporary.stat().st_size != info.file_size:
                raise KaloscopeException(ErrorCode.INTERNAL_SERVER_ERROR)
            temporary.replace(destination)
    except (BadZipFile, KeyError, OSError, RuntimeError) as exc:
        logger.warning(
            "Failed to read gallery archive member %s!%s: %s",
            archive,
            member,
            exc,
        )
        raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS) from exc
    finally:
        temporary.unlink(missing_ok=True)


async def resolve_gallery_image(value: str) -> Path:
    """Resolve a regular image or lazily cache an image stored in an archive."""
    archive_member = decode_archive_member(value)
    if archive_member is None:
        path = Path(value)
        if not path.is_file():
            raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS)
        return path

    archive, member = archive_member
    if not archive.is_file():
        raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS)
    destination = _member_cache_path(value, archive, member)
    if destination.is_file() and destination.stat().st_size > 0:
        return destination

    lock = _locks.setdefault(value, asyncio.Lock())
    async with lock:
        if destination.is_file() and destination.stat().st_size > 0:
            return destination
        async with _semaphore:
            await asyncio.to_thread(_extract_member, archive, member, destination)
            await asyncio.to_thread(
                _remove_stale_members, destination.parent, destination
            )
    return destination
