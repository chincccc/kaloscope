from dataclasses import asdict
from pathlib import Path

import aiofiles
from tortoise import timezone
from tortoise.expressions import Q
from tortoise.functions import Count, Sum
from tortoise.transactions import atomic

from app.core.constants import ENCODING
from app.core.dl.adapter import load_config
from app.core.dl.syncer import Unique, execute_download_plan
from app.core.exceptions import ErrorCode, KaloscopeException
from app.models.download import (
    ComicDownloadTask,
    DownloadAdd,
    DownloadDir,
    Downloader,
    DownloaderUpsert,
    DownloadPlan,
    DownloadPlanUpsert,
    DownloadQuery,
    DownloadState,
    DownloadStats,
    DownloadTask,
)
from app.services.base import BaseService
from app.utils.bittorrent import (
    decode_torrent,
    standardize_magnet,
)


class DownloaderService(BaseService[Downloader], model=Downloader):
    """The service class for all downloader related operations."""

    PRESETS_PATH = Path(__file__).resolve().parents[2] / "static/downloaders"

    @classmethod
    async def get_presets(cls) -> dict[str, str]:
        """Get the downloader presets.

        Returns:
            The downloader presets.
        """
        presets = {}
        for file in cls.PRESETS_PATH.iterdir():
            if file.is_file() and file.suffix == ".yaml":
                async with aiofiles.open(file, encoding=ENCODING) as f:
                    presets[file.name[:-5]] = await f.read()
        return presets

    @classmethod
    @atomic()
    async def update_priorities(cls, ids: list):
        """Update the downloader priorities.

        Args:
            ids: The sorted downloader IDs.
        """
        downloaders = await Downloader.all()
        if set(ids) != set(d.id for d in downloaders):
            raise KaloscopeException(ErrorCode.BAD_REQUEST)
        # avoid duplicate priorities
        priorities = [downloader.priority for downloader in downloaders]
        start_priority = 1 if min(priorities) > len(ids) else max(priorities) + 1
        for downloader in downloaders:
            downloader.priority = start_priority + ids.index(downloader.id)
        await Downloader.bulk_update(downloaders, fields=["priority"])

    @classmethod
    async def upsert(cls, obj: DownloaderUpsert) -> Downloader:
        """Create or update a downloader.

        Args:
            obj: The downloader data.

        Raises:
            KaloscopeException: If the name already exists.

        Returns:
            The downloader instance.
        """
        # load the YAML configuration
        adapter = load_config(obj.config)

        # check if the name already exists
        filter = Q(name=adapter.name)
        if obj.id:
            filter &= ~Q(id=obj.id)
        if await Downloader.filter(filter).count() > 0:
            raise KaloscopeException(ErrorCode.NAME_ALREADY_EXISTS)

        if obj.id:
            # update the downloader
            await Downloader.filter(id=obj.id).update(
                config=obj.config,
                name=adapter.name,
                host=adapter.host,
                port=adapter.port,
            )
            downloader = await Downloader.get(id=obj.id)
        else:
            # create the downloader
            version = await adapter.version()
            priorities: list = await Downloader.all().values_list("priority", flat=True)
            downloader = await Downloader.create(
                preset=obj.preset or None,
                config=obj.config,
                name=adapter.name,
                host=adapter.host,
                port=adapter.port,
                version=version,
                priority=(max(priorities) + 1 if priorities else 1),
            )

        return downloader


class DownloadDirService(BaseService[DownloadDir], model=DownloadDir):
    """The service class for all download directory related operations."""

    @classmethod
    async def upsert(cls, path: str) -> DownloadDir:
        """Create or update a download directory.

        Args:
            path: The download directory path.

        Returns:
            The download directory instance.
        """
        dir = await DownloadDir.get_or_none(path=path)
        if dir is not None:
            dir.last_used = timezone.now()
            await dir.save()
            return dir
        return await DownloadDir.create(path=path, last_used=timezone.now())


class DownloadTaskService(BaseService[DownloadTask], model=DownloadTask):
    """The service class for all download task related operations."""

    @classmethod
    async def hash_collision(cls, hash: str | None, hash_v2: str | None = None) -> bool:
        """Check if the info hash already exists.

        Args:
            hash: The info hash.
            hash_v2: The info hash v2.

        Returns:
            True if the info hash already exists, False otherwise.
        """
        if hash and await DownloadTask.filter(info_hash=hash).exists():
            return True
        return bool(
            hash_v2 and await DownloadTask.filter(info_hash_v2=hash_v2).exists()
        )

    @classmethod
    async def combined_page(cls, query: DownloadQuery) -> dict:
        """Return BitTorrent and built-in comic tasks in one ordered page."""
        common = []
        if query.name:
            common.append(Q(name__icontains=query.name))
        if query.state:
            common.append(Q(state=query.state))
        if query.states:
            common.append(Q(state__in=query.states))

        torrent_filters = [*common]
        if query.downloader_id:
            torrent_filters.append(Q(downloader_id=query.downloader_id))
        torrent_tasks = await DownloadTask.filter(*torrent_filters)
        torrent_items = await cls.dump_list(torrent_tasks)
        for item in torrent_items:
            item["task_type"] = "torrent"
            item["key"] = f"torrent:{item['id']}"

        comic_items = []
        if not query.downloader_id:
            from app.services.comic_download import ComicDownloadService

            comic_tasks = await ComicDownloadTask.filter(*common)
            comic_items = await ComicDownloadService.dump_list(comic_tasks)
            for item in comic_items:
                item.update(
                    {
                        "task_type": "comic",
                        "key": f"comic:{item['id']}",
                        "downloader_id": None,
                        "magnet_link": None,
                    }
                )

        items = [*torrent_items, *comic_items]
        ordering = query.ordering or "-created_at"
        descending = ordering.startswith("-")
        field = ordering.removeprefix("-")
        if field not in {"created_at", "name", "downloader_id", "state"}:
            field = "created_at"
            descending = True

        def sort_value(item: dict):
            value = item.get(field)
            if value is None:
                return (True, 0 if field == "downloader_id" else "")
            normalized = value.casefold() if isinstance(value, str) else value
            return (False, normalized)

        items.sort(key=sort_value, reverse=descending)
        total = len(items)
        if query.page_num > 0:
            start = (query.page_num - 1) * query.page_size
            items = items[start : start + query.page_size]
        return {"total": total, "items": items}

    @classmethod
    @atomic()
    async def add(cls, add: DownloadAdd) -> DownloadTask:
        """Add a download task.

        Args:
            add: The download task details.

        Returns:
            The added download task.
        """
        downloader = await Downloader.get(id=add.downloader_id)
        adapter = load_config(downloader.config)

        # call the `add_torrent` or `add_link` method
        info_hash, info_hash_v2, magnet_link, result = None, None, None, None
        if add.torrent and (torrent := decode_torrent(add.torrent.body)) is not None:
            # extract info hash from the torrent
            info_hash = torrent.info_hash
            magnet_link = torrent.magnet_link
            if await cls.hash_collision(info_hash):
                raise KaloscopeException(ErrorCode.INFO_HASH_COLLISION)
            result = await adapter.call("add_torrent", add.model_dump())
        elif add.link and (magnet := await standardize_magnet(add.link)) is not None:
            # extract info hash from the magnet link
            add.link = magnet.link
            info_hash = magnet.info_hash
            info_hash_v2 = magnet.info_hash_v2
            magnet_link = magnet.link
            if await cls.hash_collision(info_hash, info_hash_v2):
                raise KaloscopeException(ErrorCode.INFO_HASH_COLLISION)
            result = await adapter.call("add_link", add.model_dump())
        if not info_hash and not info_hash_v2:
            raise KaloscopeException(ErrorCode.GET_INFO_HASH_FAILED)

        # save the download directory
        await DownloadDirService.upsert(add.dir)
        # create the download task
        unique_id = result.get("unique_id") if isinstance(result, dict) else None
        return await DownloadTask.create(
            downloader_id=downloader.id,
            dir=add.dir,
            name=info_hash or info_hash_v2,
            unique_id=unique_id,
            info_hash=info_hash,
            info_hash_v2=info_hash_v2,
            magnet_link=magnet_link,
            state=DownloadState.PAUSED if add.pause else DownloadState.DOWNLOADING,
            transfer_lib_id=add.transfer_lib_id,
            transfer_method=add.transfer_method,
            sub_pattern=add.sub_pattern,
            sub_repl=add.sub_repl,
        )

    @classmethod
    async def pause(cls, id: int):
        """Pause a download task.

        Args:
            id: The download task ID.
        """
        task = await DownloadTask.get(id=id)
        downloader = await Downloader.get(id=task.downloader_id)
        adapter = load_config(downloader.config)
        await adapter.call("pause", asdict(Unique.from_task(task)))
        await DownloadTask.filter(id=id).update(state=DownloadState.PAUSED)

    @classmethod
    async def start(cls, id: int):
        """Start a download task.

        Args:
            id: The download task ID.
        """
        task = await DownloadTask.get(id=id)
        downloader = await Downloader.get(id=task.downloader_id)
        adapter = load_config(downloader.config)
        await adapter.call("start", asdict(Unique.from_task(task)))
        await DownloadTask.filter(id=id).update(state=DownloadState.DOWNLOADING)

    @classmethod
    async def delete(cls, id: int, local: bool = False):
        """Delete a download task.

        Args:
            id: The download task ID.
            local: Whether to delete the local files.
        """
        task = await DownloadTask.get(id=id)
        downloader = await Downloader.get(id=task.downloader_id)
        adapter = load_config(downloader.config)
        try:
            await adapter.call(
                "delete", {**asdict(Unique.from_task(task)), "local": local}
            )
        except KaloscopeException as e:
            if e.extra is not None and e.extra.get("responded"):
                # As long as the downloader responded (even with an error),
                # we treat the delete as successful. We only need to ensure
                # the delete request was delivered.
                pass
            else:
                raise

        await DownloadTask.filter(id=id).delete()

    @classmethod
    async def stats(cls) -> DownloadStats:
        """Get the download statistics.

        Returns:
            The download statistics object.
        """
        count = (
            await DownloadTask.annotate(count=Count("id"))
            .group_by("state")
            .values("state", "count")
        )
        downloading = sum(
            c["count"] for c in count if c["state"] != DownloadState.COMPLETED
        )
        completed = sum(
            c["count"] for c in count if c["state"] == DownloadState.COMPLETED
        )
        up_speed = (
            await DownloadTask.annotate(total_up=Sum("up_speed"))
            .filter(state=DownloadState.DOWNLOADING)
            .values("total_up")
        )[0]["total_up"] or 0
        dl_speed = (
            await DownloadTask.annotate(total_dl=Sum("dl_speed"))
            .filter(state=DownloadState.DOWNLOADING)
            .values("total_dl")
        )[0]["total_dl"] or 0
        comic_count = (
            await ComicDownloadTask.annotate(count=Count("id"))
            .group_by("state")
            .values("state", "count")
        )
        downloading += sum(
            item["count"]
            for item in comic_count
            if item["state"] != DownloadState.COMPLETED
        )
        completed += sum(
            item["count"]
            for item in comic_count
            if item["state"] == DownloadState.COMPLETED
        )
        comic_speed = (
            await ComicDownloadTask.annotate(total_dl=Sum("dl_speed"))
            .filter(state=DownloadState.DOWNLOADING)
            .values("total_dl")
        )
        if comic_speed:
            dl_speed += comic_speed[0]["total_dl"] or 0
        return DownloadStats(
            downloading=downloading,
            completed=completed,
            up_speed=up_speed,
            dl_speed=dl_speed,
        )


class DownloadPlanService(BaseService[DownloadPlan], model=DownloadPlan):
    """The service class for all download plan related operations."""

    @classmethod
    async def upsert(cls, obj: DownloadPlanUpsert) -> DownloadPlan:
        """Create or update a download plan.

        Args:
            obj: The download plan data.

        Returns:
            The download plan instance.
        """
        data = obj.model_dump(exclude={"id"})
        if obj.id:
            await DownloadPlan.filter(id=obj.id).update(**data)
            plan = await DownloadPlan.get(id=obj.id)
        else:
            plan = await DownloadPlan.create(**data)
            # execute the plan immediately if it's active
            if not plan.inactive():
                await execute_download_plan(plan)
        return plan
