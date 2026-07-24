import asyncio
import os
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import monotonic
from zipfile import BadZipFile, ZipFile

from sanic.log import logger
from tortoise.expressions import Q
from tortoise.transactions import atomic

from app.core.exceptions import ErrorCode, KaloscopeException
from app.core.gallery_archive import (
    ARCHIVE_EXTENSIONS,
    MAX_ARCHIVE_IMAGE_SIZE,
    decode_archive_member,
    encode_archive_member,
)
from app.core.media.filename_tags import filename_tags, tagged_resource_name
from app.core.resource_rename import (
    rename_destination,
    rename_paths,
    replace_path_prefix,
    rollback_paths,
)
from app.models.gallery import Gallery, GalleryItem, GalleryUpsert
from app.models.rating import (
    RatingResourceType,
    ResourceRating,
    gallery_rating_key,
)
from app.models.user import PermType, UserPermission
from app.services.base import BaseService

IMAGE_EXTENSIONS = frozenset(
    {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
)
NATURAL_PARTS = re.compile(r"(\d+)")
CHAPTER_SEPARATORS = re.compile(r"[\s_-]+")
CHAPTER_NUMBER_PREFIX = re.compile(r"第(?=\d)")


def natural_sort_key(value: str) -> tuple[tuple[int, str | int], ...]:
    """Return a case-insensitive key that compares digit runs numerically."""
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in NATURAL_PARTS.split(value)
    )


def chapter_name_sort_key(value: str) -> tuple[tuple[int, str | int], ...]:
    """Naturally sort chapter names despite common numbering separators."""
    normalized = CHAPTER_SEPARATORS.sub("", value)
    normalized = CHAPTER_NUMBER_PREFIX.sub("", normalized)
    return natural_sort_key(normalized)


def chapter_directory_sort_key(
    book_root: str, directory: str
) -> tuple[tuple[tuple[int, str | int], ...], ...]:
    """Naturally sort a chapter by each relative folder name."""
    return tuple(
        chapter_name_sort_key(part) for part in relative_parts(book_root, directory)
    )


def relative_parts(root: str, directory: str) -> tuple[str, ...]:
    """Return a directory path relative to a gallery or book root."""
    try:
        return Path(directory).relative_to(root).parts
    except ValueError:
        return (Path(directory).name,)


def gallery_book_key(root: str, directory: str) -> str | None:
    """Return the first-level folder that owns an image.

    Images stored directly in the gallery root belong to the uncategorized book.
    """
    parts = relative_parts(root, directory)
    return parts[0] if parts else None


def replace_gallery_item_path(value: str, source: Path, destination: Path) -> str:
    """Replace a filesystem prefix in a regular or archived image path."""
    archive_member = decode_archive_member(value)
    if archive_member is None:
        return replace_path_prefix(value, source, destination) or value
    archive, member = archive_member
    replaced = replace_path_prefix(str(archive), source, destination)
    return encode_archive_member(Path(replaced or archive), member)


def discover_images(directory: str) -> dict[str, tuple[str, str, int, datetime]]:
    """Return supported image files and image members of ZIP/CBZ archives."""
    images = {}
    for path in Path(directory).rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            resolved = str(path.resolve())
            images[resolved] = (
                str(path.parent.resolve()),
                path.name,
                stat.st_size,
                datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            )
        elif suffix in ARCHIVE_EXTENSIONS:
            images.update(
                _discover_archive_images(Path(directory), path, stat.st_mtime)
            )
    return images


def _discover_archive_images(
    root: Path, archive: Path, modified_timestamp: float
) -> dict[str, tuple[str, str, int, datetime]]:
    """Return virtual GalleryItem rows for supported images in one archive."""
    try:
        with ZipFile(archive) as zip_file:
            members = []
            for info in zip_file.infolist():
                member_path = PurePosixPath(info.filename.replace("\\", "/"))
                if (
                    info.is_dir()
                    or info.flag_bits & 0x1
                    or not 0 < info.file_size <= MAX_ARCHIVE_IMAGE_SIZE
                    or member_path.suffix.lower() not in IMAGE_EXTENSIONS
                    or "__MACOSX" in member_path.parts
                    or ".." in member_path.parts
                    or member_path.is_absolute()
                ):
                    continue
                members.append((info, member_path))
    except (BadZipFile, OSError, RuntimeError):
        logger.warning("Ignoring unreadable gallery archive %s", archive)
        return {}
    if not members:
        return {}

    member_paths = [member_path for _, member_path in members]
    first_parts = {parts.parts[0] for parts in member_paths if len(parts.parts) > 1}
    strip_wrapper = len(first_parts) == 1 and all(
        len(parts.parts) > 1 for parts in member_paths
    )
    wrapper = next(iter(first_parts)) if strip_wrapper else None

    resolved_root = root.resolve()
    resolved_archive = archive.resolve()
    relative_archive = resolved_archive.relative_to(resolved_root)
    if len(relative_archive.parts) == 1:
        logical_base = resolved_root / archive.stem
    else:
        logical_base = resolved_root / relative_archive.parts[0]
        nested = relative_archive.parts[1:-1]
        logical_base = logical_base.joinpath(*nested, archive.stem)

    modified_at = datetime.fromtimestamp(modified_timestamp, tz=UTC)
    discovered = {}
    for info, member_path in members:
        relative = member_path
        if wrapper is not None:
            relative = PurePosixPath(*member_path.parts[1:])
        member_parent = (
            relative.parent.parts if relative.parent != PurePosixPath(".") else ()
        )
        logical_dir = logical_base.joinpath(*member_parent)
        encoded = encode_archive_member(resolved_archive, info.filename)
        if (
            len(encoded) > 4096
            or len(str(logical_dir)) > 4096
            or len(relative.name) > 255
        ):
            continue
        discovered[encoded] = (
            str(logical_dir),
            relative.name,
            info.file_size,
            modified_at,
        )
    return discovered


def build_book_index(root: str, values: list[dict]) -> list[dict]:
    """Group image rows into a naturally sorted first-folder book index."""
    grouped: dict[str | None, list[dict]] = {}
    for item in values:
        key = gallery_book_key(root, item["dir"])
        grouped.setdefault(key, []).append(item)

    books = []
    for key, items in grouped.items():
        sorted_items = sorted(
            items,
            key=lambda item: natural_sort_key(
                "/".join((*relative_parts(root, item["dir"]), item["name"]))
            ),
        )
        books.append(
            {
                "id": sorted_items[0]["id"],
                "name": key,
                "tags": filename_tags(key or ""),
                "item_count": len(sorted_items),
                "uncategorized": key is None,
            }
        )

    books.sort(
        key=lambda book: (
            book["uncategorized"],
            natural_sort_key(book["name"] or ""),
        )
    )
    return books


class GalleryService(BaseService[Gallery], model=Gallery):
    _scanning_ids: set[int] = set()

    _scan_tasks: dict[int, asyncio.Task] = {}
    _last_scans: dict[int, float] = {}
    _book_cache: dict[int, list[dict]] = {}
    _RENAME_LOCK = asyncio.Lock()

    @classmethod
    def is_scanning(cls, gallery_id: int) -> bool:
        return gallery_id in cls._scanning_ids or gallery_id in cls._scan_tasks

    @classmethod
    @atomic()
    async def update_priorities(cls, ids: list):
        galleries = await Gallery.all()
        if set(ids) != {gallery.id for gallery in galleries}:
            raise KaloscopeException(ErrorCode.BAD_REQUEST)
        priorities = [gallery.priority for gallery in galleries]
        start = 1 if min(priorities) > len(ids) else max(priorities) + 1
        for gallery in galleries:
            gallery.priority = start + ids.index(gallery.id)
        await Gallery.bulk_update(galleries, fields=["priority"])

    @classmethod
    @atomic()
    async def upsert(cls, obj: GalleryUpsert) -> Gallery:
        query = ~Q(id=obj.id) if obj.id else Q()
        if await Gallery.filter(query & Q(name=obj.name)).exists():
            raise KaloscopeException(ErrorCode.NAME_ALREADY_EXISTS)
        if obj.dir:
            directory = Path(obj.dir).resolve()
            dirs: list[str] = await Gallery.filter(query).values_list("dir", flat=True)
            for existing_dir in dirs:
                existing = Path(existing_dir).resolve()
                if directory.is_relative_to(existing) or existing.is_relative_to(
                    directory
                ):
                    raise KaloscopeException(ErrorCode.DUPLICATE_DIRECTORY)

        if obj.id:
            await Gallery.filter(id=obj.id).update(name=obj.name)
            return await Gallery.get(id=obj.id)

        priorities: list[int] = await Gallery.all().values_list("priority", flat=True)
        return await Gallery.create(
            dir=str(Path(obj.dir).resolve()),
            name=obj.name,
            priority=max(priorities) + 1 if priorities else 1,
        )

    @classmethod
    @atomic()
    async def delete(cls, id: int):
        task = cls._scan_tasks.pop(id, None)
        if task:
            task.cancel()
        cls._book_cache.pop(id, None)
        cls._last_scans.pop(id, None)
        await Gallery.filter(id=id).delete()
        await UserPermission.filter(rel_type=PermType.GALLERY, rel_id=id).delete()

    @classmethod
    def request_scan(cls, id: int, *, min_interval: float = 0) -> bool:
        """Schedule a non-blocking scan unless one is active or recently finished."""
        if cls.is_scanning(id):
            return False
        last_scan = cls._last_scans.get(id)
        if last_scan is not None and monotonic() - last_scan < min_interval:
            return False
        cls._scan_tasks[id] = asyncio.create_task(cls._background_scan(id))
        return True

    @classmethod
    async def _background_scan(cls, id: int):
        try:
            await cls.scan(id)
        except Exception:
            logger.error("Failed to scan gallery %s", id, exc_info=True)
        finally:
            cls._scan_tasks.pop(id, None)

    @classmethod
    async def book_index(cls, id: int, root: str) -> list[dict]:
        """Return the cached book index, falling back to persisted rows."""
        cached = cls._book_cache.get(id)
        if cached is not None:
            return cached
        values = await GalleryItem.filter(gallery_id=id).values("id", "dir", "name")
        books = build_book_index(root, values)
        cls._book_cache[id] = books
        return books

    @classmethod
    async def rename_book(cls, item_id: int, name: str) -> dict:
        """Rename the first-level directory represented by a gallery book."""
        async with cls._RENAME_LOCK:
            item = await GalleryItem.get(id=item_id).select_related("gallery")
            gallery = item.gallery
            if cls.is_scanning(gallery.id):
                raise KaloscopeException(ErrorCode.SCAN_IN_PROGRESS)

            book_key = gallery_book_key(gallery.dir, item.dir)
            if book_key is None:
                raise KaloscopeException(ErrorCode.BAD_REQUEST)
            old_rating_key = gallery_rating_key(gallery.id, book_key)
            root = Path(gallery.dir).resolve()
            source = (root / book_key).resolve()
            archive_member = decode_archive_member(item.path)
            root_archive = None
            if archive_member is not None:
                archive = archive_member[0].resolve()
                if archive.parent == root and archive.stem == book_key:
                    root_archive = archive

            if root_archive is not None:
                source = root_archive
                destination = rename_destination(source, name, directory=False)
                source_dir = root / book_key
                destination_dir = root / destination.stem
            else:
                if source.parent != root:
                    raise KaloscopeException(ErrorCode.BAD_REQUEST)
                if not source.is_dir():
                    raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS)
                destination = rename_destination(source, name, directory=True)
                source_dir = source
                destination_dir = destination

            moves = [(source, destination)]
            await rename_paths(moves)
            try:
                prefix = f"{source_dir}{os.sep}"
                items = await GalleryItem.filter(gallery_id=gallery.id).filter(
                    Q(dir=source_dir) | Q(dir__startswith=prefix)
                )
                for current in items:
                    current.path = replace_gallery_item_path(
                        current.path, source, destination
                    )
                    current.dir = replace_path_prefix(
                        current.dir, source_dir, destination_dir
                    )
                if items:
                    await GalleryItem.bulk_update(
                        items, fields=["path", "dir"], batch_size=500
                    )
                await ResourceRating.filter(
                    resource_type=RatingResourceType.GALLERY_BOOK.value,
                    resource_key=old_rating_key,
                ).update(
                    resource_key=gallery_rating_key(
                        gallery.id,
                        destination.stem
                        if root_archive is not None
                        else destination.name,
                    )
                )
            except Exception:
                await rollback_paths(moves)
                raise

            values = await GalleryItem.filter(gallery_id=gallery.id).values(
                "id", "dir", "name"
            )
            cls._book_cache[gallery.id] = build_book_index(gallery.dir, values)
            return {
                "id": item_id,
                "name": destination.stem
                if root_archive is not None
                else destination.name,
                "tags": filename_tags(
                    destination.stem if root_archive is not None else destination.name
                ),
            }

    @classmethod
    async def set_book_tags(cls, item_id: int, tags: list[str]) -> dict:
        """Persist gallery book tags in its real first-level directory name."""
        item = await GalleryItem.get(id=item_id).select_related("gallery")
        book_key = gallery_book_key(item.gallery.dir, item.dir)
        if book_key is None:
            raise KaloscopeException(ErrorCode.BAD_REQUEST)
        name = tagged_resource_name(book_key, tags, directory=True)
        return await cls.rename_book(item_id, name)

    @classmethod
    async def scan(cls, id: int):
        if id in cls._scanning_ids:
            raise KaloscopeException(ErrorCode.SCAN_IN_PROGRESS)
        cls._scanning_ids.add(id)
        try:
            gallery = await Gallery.get(id=id)
            discovered = await asyncio.to_thread(discover_images, gallery.dir)
            existing = await GalleryItem.filter(gallery_id=id)
            path_items = {item.path: item for item in existing}
            creates = []
            updates = []

            for path, (directory, name, size, modified_at) in discovered.items():
                item = path_items.get(path)
                if item is None:
                    creates.append(
                        GalleryItem(
                            gallery_id=id,
                            dir=directory,
                            path=path,
                            name=name,
                            size=size,
                            modified_at=modified_at,
                        )
                    )
                elif item.size != size or item.modified_at != modified_at:
                    item.dir = directory
                    item.name = name
                    item.size = size
                    item.modified_at = modified_at
                    updates.append(item)

            missing_ids = [item.id for item in existing if item.path not in discovered]
            if missing_ids:
                await GalleryItem.filter(id__in=missing_ids).delete()
            if creates:
                await GalleryItem.bulk_create(creates, batch_size=500)
            if updates:
                await GalleryItem.bulk_update(
                    updates,
                    fields=["dir", "name", "size", "modified_at"],
                    batch_size=500,
                )
            values = await GalleryItem.filter(gallery_id=id).values("id", "dir", "name")
            cls._book_cache[id] = build_book_index(gallery.dir, values)
            cls._last_scans[id] = monotonic()
        finally:
            cls._scanning_ids.discard(id)


class GalleryItemService(BaseService[GalleryItem], model=GalleryItem):
    pass
