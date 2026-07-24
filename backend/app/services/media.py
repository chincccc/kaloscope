import asyncio
import hashlib
import os
from pathlib import Path

import aiofiles
from sanic import Sanic
from sanic.log import logger
from tortoise.expressions import Q
from tortoise.transactions import atomic

from app.core.exceptions import ErrorCode, KaloscopeException
from app.core.media.filename_tags import filename_tags, tagged_resource_name
from app.core.media.handlers.base import MediaPathInfo
from app.core.resource_rename import (
    rename_destination,
    rename_paths,
    replace_path_prefix,
    rollback_paths,
    sidecar_destination,
)
from app.core.transcode import probe_media
from app.models.flow import FlowTrigger, GraphCategory
from app.models.media import (
    LibType,
    MediaItem,
    MediaLib,
    MediaLibUpsert,
    MediaMetadata,
    NFOType,
)
from app.models.user import PermType, UserPermission
from app.services.base import BaseService
from app.services.flow import FlowTriggerService
from app.utils.disk import delete_path


class MediaLibService(BaseService[MediaLib], model=MediaLib):
    """The service class for all media library related operations."""

    @classmethod
    @atomic()
    async def update_priorities(cls, ids: list):
        """Update the media library priorities.

        Args:
            ids: The sorted media library IDs.
        """
        libs = await MediaLib.all()
        if set(ids) != set(lib.id for lib in libs):
            raise KaloscopeException(ErrorCode.BAD_REQUEST)
        # avoid duplicate priorities
        priorities = [lib.priority for lib in libs]
        start_priority = 1 if min(priorities) > len(ids) else max(priorities) + 1
        for lib in libs:
            lib.priority = start_priority + ids.index(lib.id)
        await MediaLib.bulk_update(libs, fields=["priority"])

    @classmethod
    @atomic()
    async def upsert(cls, obj: MediaLibUpsert) -> MediaLib:
        """Create or update a media library.

        Args:
            obj: The media library data.

        Raises:
            KaloscopeException: If the name or directory already exists.

        Returns:
            The media library instance.
        """

        # check if the name already exists
        filter = ~Q(id=obj.id) if obj.id else Q()
        if await MediaLib.filter(filter & Q(name=obj.name)).count() > 0:
            raise KaloscopeException(ErrorCode.NAME_ALREADY_EXISTS)
        # check if the directory overlaps with existing ones
        if obj.dir:
            dir = Path(obj.dir)
            dirs: list = await MediaLib.filter(filter).values_list("dir", flat=True)
            for d in dirs:
                existing = Path(d)
                if dir.is_relative_to(existing) or existing.is_relative_to(dir):
                    raise KaloscopeException(ErrorCode.DUPLICATE_DIRECTORY)

        if obj.id:
            # update the media library
            await MediaLib.filter(id=obj.id).update(
                name=obj.name,
                language=obj.language or None,
                danmaku_server=obj.danmaku_server,
                danmaku_ttl=obj.danmaku_ttl,
            )
            lib = await MediaLib.get(id=obj.id)
        else:
            # create the media library
            priorities: list = await MediaLib.all().values_list("priority", flat=True)
            lib = await MediaLib.create(
                lib_type=obj.lib_type,
                dir=obj.dir,
                name=obj.name,
                language=obj.language or None,
                danmaku_server=obj.danmaku_server,
                danmaku_ttl=obj.danmaku_ttl,
                priority=(max(priorities) + 1 if priorities else 1),
            )
            # add the observer
            watcher = cls.app_ctx().lib_watcher
            await watcher.add_observer(lib)

        # bind the flow triggers to the media library
        await FlowTriggerService.bind_triggers(
            GraphCategory.INGEST, lib.id, obj.triggers
        )

        return lib

    @classmethod
    @atomic()
    async def delete(cls, id: int):
        """Delete a media library.

        Args:
            id: The media library ID.
        """
        lib = await MediaLib.get(id=id)
        await MediaLib.filter(id=id).delete()
        await FlowTrigger.filter(category=GraphCategory.INGEST, rel_id=id).delete()
        await UserPermission.filter(rel_type=PermType.MEDIA_LIB, rel_id=id).delete()
        # remove the observer
        watcher = cls.app_ctx().lib_watcher
        await watcher.remove_observer(lib.dir)


class MediaItemService(BaseService[MediaItem], model=MediaItem):
    """The service class for all media item related operations."""

    HASH_READ_SIZE = 16 * 1024 * 1024  # 16MB
    PROBE_SEMAPHORE = asyncio.Semaphore(2)
    _technical_backfill_ids: set[int] = set()
    _background_tasks: set[asyncio.Task] = set()
    _RENAME_LOCK = asyncio.Lock()

    @classmethod
    def _attach_filename_tags(cls, value):
        if isinstance(value, dict):
            path = value.get("path")
            name = value.get("name")
            if isinstance(path, str):
                value["tags"] = filename_tags(Path(path).name)
            elif isinstance(name, str):
                value["tags"] = filename_tags(name)
            for child in value.values():
                cls._attach_filename_tags(child)
        elif isinstance(value, list):
            for child in value:
                cls._attach_filename_tags(child)
        return value

    @classmethod
    async def dump(cls, obj, **kwargs):
        return cls._attach_filename_tags(await super().dump(obj, **kwargs))

    @classmethod
    async def dump_list(cls, list, **kwargs):
        return cls._attach_filename_tags(await super().dump_list(list, **kwargs))

    @classmethod
    async def delete(cls, id: int, local: bool = False):
        """Delete a media item.

        Args:
            id: The media item ID.
            local: Whether to delete the local files.
        """
        if local:
            item = await MediaItem.get(id=id)
            path = Path(item.path)
            if path.exists():
                delete_path(path)
            await item.delete()
        else:
            await MediaItem.filter(id=id).update(visible=False)

    @classmethod
    async def rename(cls, id: int, name: str) -> MediaItem:
        """Rename a movie, episode, or top-level TV show on disk."""
        async with cls._RENAME_LOCK:
            item = await MediaItem.get(id=id).select_related("lib")
            directory = item.lib.lib_type == LibType.TV_SHOW and item.parent_id is None
            source = Path(item.path)
            root = Path(item.lib.dir).resolve()
            try:
                source = source.resolve()
            except OSError as error:
                raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS) from error
            if source == root or not source.is_relative_to(root):
                raise KaloscopeException(ErrorCode.BAD_REQUEST)
            if directory:
                if not source.is_dir():
                    raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS)
            elif not source.is_file():
                code = (
                    ErrorCode.BAD_REQUEST
                    if source.exists()
                    else ErrorCode.FILE_NOT_EXISTS
                )
                raise KaloscopeException(code)

            destination = rename_destination(source, name, directory=directory)
            moves = [(source, destination)]
            path_destinations = {str(source): str(destination)}
            seen_paths = {source}
            if not directory:
                for value in (item.nfo_path, item.danmaku_path):
                    if not value:
                        continue
                    sidecar = Path(value)
                    if not sidecar.is_file():
                        continue
                    if sidecar in seen_paths:
                        continue
                    sidecar_target = sidecar_destination(sidecar, source, destination)
                    if sidecar_target == sidecar:
                        continue
                    moves.append((sidecar, sidecar_target))
                    seen_paths.add(sidecar)
                    path_destinations[str(sidecar)] = str(sidecar_target)

            await rename_paths(moves)
            try:
                if directory:
                    prefix = f"{source}{os.sep}"
                    items = await MediaItem.filter(
                        Q(path=str(source)) | Q(path__startswith=prefix)
                    )
                    for current in items:
                        current.path = replace_path_prefix(
                            current.path, source, destination
                        )
                        current.dir = replace_path_prefix(
                            current.dir, source, destination
                        )
                        current.nfo_path = replace_path_prefix(
                            current.nfo_path, source, destination
                        )
                        current.danmaku_path = replace_path_prefix(
                            current.danmaku_path, source, destination
                        )
                        if current.id == item.id:
                            current.name = destination.name
                    if items:
                        await MediaItem.bulk_update(
                            items,
                            fields=[
                                "path",
                                "dir",
                                "name",
                                "nfo_path",
                                "danmaku_path",
                            ],
                        )
                else:
                    item.path = str(destination)
                    item.dir = str(destination.parent)
                    item.name = destination.name
                    item.nfo_path = path_destinations.get(item.nfo_path, item.nfo_path)
                    item.danmaku_path = path_destinations.get(
                        item.danmaku_path, item.danmaku_path
                    )
                    await item.save(
                        update_fields=[
                            "path",
                            "dir",
                            "name",
                            "nfo_path",
                            "danmaku_path",
                        ]
                    )
            except Exception:
                await rollback_paths(moves)
                raise
            return await MediaItem.get(id=id).select_related("lib")

    @classmethod
    async def set_tags(cls, id: int, tags: list[str]) -> MediaItem:
        """Persist tags by rewriting the resource's real filename."""
        item = await MediaItem.get(id=id).select_related("lib")
        directory = item.lib.lib_type == LibType.TV_SHOW and item.parent_id is None
        name = tagged_resource_name(Path(item.path).name, tags, directory=directory)
        return await cls.rename(id, name)

    @classmethod
    async def create(
        cls,
        lib_id: int,
        *,
        path_info: MediaPathInfo,
        parent_id: int | None = None,
        default_title: str | None = None,
    ) -> MediaItem:
        """Get or create a media item.

        Args:
            lib_id: The media library ID.
            path_info: The media path info object.
            parent_id: The parent media item ID, if any.
            default_title: The default title to use if the media item is created.

        Returns:
            The media item instance.
        """
        item_path = path_info.item_path
        path = Path(item_path)
        size = path.stat().st_size if path.is_file() else None
        item, _ = await MediaItem.get_or_create(
            lib_id=lib_id,
            path=item_path,
            defaults={
                "parent_id": parent_id,
                "dir": path_info.item_dir,
                "name": path_info.item_name,
                "title": default_title,
                "year": path_info.year,
                "season": path_info.season,
                "episode": path_info.episode,
                "visible": True,
                "size": size,
            },
        )
        return item

    @classmethod
    async def ensure_technical_metadata(cls, item: MediaItem) -> MediaItem:
        """Probe and cache the technical metadata for one playable file."""
        if item.duration is not None:
            return item
        path = Path(item.path)
        if not path.is_file():
            return item

        try:
            async with cls.PROBE_SEMAPHORE:
                metadata = await probe_media(item.path)
            size = item.size if item.size is not None else path.stat().st_size
            duration = (
                metadata.duration
                if metadata.duration and metadata.duration > 0
                else 0.0
            )
            bitrate = metadata.bitrate
            if bitrate is None and duration > 0:
                bitrate = round(size * 8 / duration)
            values = {
                "size": size,
                "duration": duration,
                "width": metadata.width,
                "height": metadata.height,
                "bitrate": bitrate,
            }
            await MediaItem.filter(id=item.id).update(**values)
            for key, value in values.items():
                setattr(item, key, value)
        except Exception:
            logger.warning(
                "Failed to probe technical metadata for media item %s",
                item.id,
                exc_info=True,
            )
        return item

    @classmethod
    def request_technical_backfill(cls, items: list[MediaItem]) -> bool:
        """Queue missing technical metadata without delaying the caller."""
        pending = [
            item
            for item in items
            if item.duration is None
            and item.size is not None
            and item.id not in cls._technical_backfill_ids
        ]
        if not pending:
            return False
        cls._technical_backfill_ids.update(item.id for item in pending)
        task = asyncio.create_task(cls._run_technical_backfill(pending))
        cls._background_tasks.add(task)
        task.add_done_callback(cls._background_tasks.discard)
        return True

    @classmethod
    async def _run_technical_backfill(cls, items: list[MediaItem]):
        try:
            for offset in range(0, len(items), 8):
                await asyncio.gather(
                    *(
                        cls.ensure_technical_metadata(item)
                        for item in items[offset : offset + 8]
                    )
                )
        finally:
            cls._technical_backfill_ids.difference_update(item.id for item in items)

    @staticmethod
    def technical_summary(items: list[MediaItem]) -> dict[str, int | float | None]:
        """Aggregate duration, size, bitrate, and peak resolution."""
        playable = [item for item in items if item.duration is not None]
        durations = [item.duration or 0 for item in playable]
        duration = sum(durations)
        sizes = [item.size for item in playable if item.size is not None]
        size = sum(sizes) if sizes else None
        resolution = max(
            (
                item
                for item in playable
                if item.width is not None and item.height is not None
            ),
            key=lambda item: (item.width or 0) * (item.height or 0),
            default=None,
        )
        bitrate = (
            round(size * 8 / duration) if size is not None and duration > 0 else None
        )
        return {
            "duration": duration,
            "size": size,
            "width": resolution.width if resolution else None,
            "height": resolution.height if resolution else None,
            "bitrate": bitrate,
        }

    @classmethod
    async def resolve_media_hash(cls, item_path: str) -> str:
        """Look up the media file's hash from the database.

        Args:
            item_path: The file path of the media item.

        Returns:
            The media hash if found, otherwise calculate and return the hash.
        """
        try:
            media = await MediaItem.filter(path=item_path).first()
            if media and media.hash:
                return media.hash
        except Exception:
            logger.debug(
                "Failed to look up media hash for '%s'", item_path, exc_info=True
            )

        # fallback to calculating the hash if not found in the database
        path = Path(item_path)
        if not path.is_file():
            raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS)
        md5 = hashlib.md5()
        async with aiofiles.open(path, "rb") as f:
            md5.update(await f.read(cls.HASH_READ_SIZE))
        return md5.hexdigest()

    @classmethod
    async def refresh_episodes(cls, item: MediaItem, meta: MediaMetadata):
        """Refresh the metadata of the episodes under a season.

        Args:
            item: The season media item.
            meta: The season metadata object.
        """
        from app.core.media.shelver import gen_nfo, get_nfo_path

        metadata = meta.metadata
        series_id = metadata.get("id")
        nfo_source = metadata.get("site")
        season = metadata.get("season", item.season)
        title = metadata.get("title", item.title)
        year = metadata.get("year", item.year)

        # check if the series_id is the same as the current one
        same_series = series_id and str(series_id) == str(item.unique_id)

        # get the flow engine from the app context
        engine = Sanic.get_app().ctx.flow_engine

        # get the episodes under the season
        episodes = await MediaItem.filter(parent_id=item.id)
        for e in episodes:
            episode = e.episode
            nfo_path = e.nfo_path

            # skip if the season is the same and the NFO file already exists
            if same_series and nfo_path and Path(nfo_path).exists():
                same_season = e.season == season
                if same_season:
                    continue

            # execute the flow to get the metadata for the episode
            results = await engine.execute(
                graph_id=meta.graph_id,
                bootparams={
                    "$manual": True,
                    "series_id": series_id,
                    "nfo_source": nfo_source,
                    "item_path": e.path,
                    "item_name": e.name,
                    "nfo_type": NFOType.EPISODE,
                    "language": item.lib.language,
                    "title": title,
                    "year": year,
                    "season": season,
                    "episode": episode,
                    "page_num": 1,
                    "page_size": 1,
                },
            )

            # generate the NFO file for the episode
            if isinstance(results, list) and len(results) > 0:
                result = results[0]
                if isinstance(result, dict):
                    result["season"] = _s if (_s := result.get("season")) else season
                    result["episode"] = _e if (_e := result.get("episode")) else episode
                    nfo_path = nfo_path or get_nfo_path(e.path)
                    await gen_nfo(NFOType.EPISODE, nfo_path, result, overwrite=True)
