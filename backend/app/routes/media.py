import asyncio
import mimetypes
import re
from pathlib import Path

import httpx
from aiofiles import os as async_os
from curl_cffi import AsyncSession
from curl_cffi.requests.exceptions import RequestException
from sanic import Blueprint, HTTPResponse, Request, empty, json, raw, redirect
from sanic.exceptions import InvalidRangeType, RangeNotSatisfiable
from sanic.log import logger
from sanic.response import ResponseStream, file_stream
from sanic_ext import validate
from tortoise.expressions import Q, RawSQL

from app.core.config import KaloscopeConfig
from app.core.decorators import authorize
from app.core.exceptions import (
    BadRequestException,
    ErrorCode,
    ForbiddenException,
    KaloscopeException,
)
from app.core.media.editor import (
    delete_custom_thumbnail,
    edit_nfo,
    extract_video_frame,
    frame_positions,
    save_custom_thumbnail,
    save_video_frame,
)
from app.core.media.screenshots import (
    current_screenshot_path,
    request_screenshots,
)
from app.core.media.shelver import (
    gen_nfo,
    get_nfo_path,
    get_nfo_type,
    parse_nfo,
    update_metadata,
)
from app.core.media.thumbnails import (
    POSTER_SOURCE_AUTO,
    THUMBNAIL_SOURCE_FIRST,
    classify_unmarked_poster,
    thumbnail_position,
    thumbnail_source,
)
from app.core.media.watcher import LibWatcher
from app.core.network import resolve_proxy
from app.core.transcode import (
    delete_tasks,
    ensure_transcode,
    list_tasks,
    output_dir,
    probe_media,
    read_m3u8,
    stop_tasks,
)
from app.models.base import IDs, Range, ResourceRename
from app.models.flow import GraphCategory
from app.models.media import (
    LibType,
    MediaDel,
    MediaFeedQuery,
    MediaFrameQuery,
    MediaFramesQuery,
    MediaItem,
    MediaLib,
    MediaLibUpsert,
    MediaMetadata,
    MediaMetadataEdit,
    MediaQuery,
    MediaResource,
    MediaTags,
    NFOType,
    TranscodeQuery,
    TranscodeTaskQuery,
    feed_excluded_ids,
)
from app.models.user import UserInfo, UserRole
from app.services.flow import FlowTriggerService
from app.services.media import MediaItemService, MediaLibService
from app.services.rating import resource_rating_values
from app.utils.extractor import extract_title
from app.utils.proxy import (
    HLS_CONTENT_TYPES,
    PROXY_RESPONSE_HEADERS,
    RemoteProxy,
    remote_proxy_request,
    rewrite_hls_playlist,
)

media = Blueprint("media", url_prefix="/media")
thumbnail_semaphore = asyncio.Semaphore(2)


async def ensure_default_thumbnail(item: MediaItem):
    """Persist a thumbnail from the configured position when artwork is missing."""
    if item.poster:
        if item.poster_source:
            return
        if await classify_unmarked_poster(item) != POSTER_SOURCE_AUTO:
            return
        old_poster = item.poster
        cleared = await MediaItem.filter(
            id=item.id,
            poster=old_poster,
            poster_source=POSTER_SOURCE_AUTO,
        ).update(poster=None, poster_source=None)
        if not cleared:
            return
        item.poster = None
        await delete_custom_thumbnail(old_poster)

    source = item
    if not Path(source.path).is_file():
        source = (
            await MediaItem.filter(parent_id=item.id, visible=True)
            .order_by("season", "episode", "name")
            .first()
        )
    if not source or not Path(source.path).is_file():
        return

    poster = None
    try:
        source_setting = await thumbnail_source()
        if source_setting != THUMBNAIL_SOURCE_FIRST:
            source = await MediaItemService.ensure_technical_metadata(source)
        position = thumbnail_position(source_setting, source.duration)
        async with thumbnail_semaphore:
            poster = await save_video_frame(source.path, position)
        updated = (
            await MediaItem.filter(id=item.id)
            .filter(Q(poster__isnull=True) | Q(poster=""))
            .update(poster=poster, poster_source=POSTER_SOURCE_AUTO)
        )
        if updated:
            item.poster = poster
            item.poster_source = POSTER_SOURCE_AUTO
            poster = None
    except Exception:
        logger.warning(
            "Failed to generate a default thumbnail for media item %s",
            item.id,
            exc_info=True,
        )
    finally:
        await delete_custom_thumbnail(poster)


async def media_technical_summaries(
    items: list[MediaItem],
) -> dict[int, dict[str, int | float | None]]:
    """Probe missing files and summarize top-level movie or show metadata."""
    if not items:
        return {}
    lib_types = dict(
        await MediaLib.filter(id__in={item.lib_id for item in items}).values_list(
            "id", "lib_type"
        )
    )
    children = await MediaItem.filter(
        parent_id__in=[item.id for item in items], visible=True
    )
    by_parent: dict[int, list[MediaItem]] = {}
    for child in children:
        if child.parent_id is not None:
            by_parent.setdefault(child.parent_id, []).append(child)
    sources = [item for item in items if Path(item.path).is_file()] + children
    MediaItemService.request_technical_backfill(sources)

    summaries = {}
    for item in items:
        episodes = by_parent.get(item.id, [])
        summary = MediaItemService.technical_summary(episodes if episodes else [item])
        summary["episode_count"] = (
            len(episodes) if lib_types.get(item.lib_id) == LibType.TV_SHOW else None
        )
        summaries[item.id] = summary
    return summaries


@media.get("/lib/list")
@authorize()
async def list_libraries(request: Request) -> HTTPResponse:
    """List the media libraries."""
    queries = []
    # filter the libraries by the user's permissions
    user: UserInfo = request.ctx.user
    if user.perms is not None:
        queries.append(Q(id__in=user.perms.media_lib_ids))
    # list the libraries without pagination
    media_libs = await MediaLibService.dump_list(MediaLib.filter(*queries))
    # attach the triggers and scanning status for each library
    watcher: LibWatcher = request.app.ctx.lib_watcher
    for lib in media_libs:
        lib["triggers"] = await FlowTriggerService.get_triggers(
            GraphCategory.INGEST, lib["id"]
        )
        lib["scanning"] = watcher.is_scanning(lib["dir"])
    return json(media_libs)


@media.post("/lib/sort")
@validate(json=IDs)
async def sort_libraries(_, body: IDs) -> HTTPResponse:
    """Sort the media libraries."""
    await MediaLibService.update_priorities(body.ids)
    return empty()


@media.post("/lib/upsert")
@authorize(role=UserRole.ADMIN)
@validate(json=MediaLibUpsert)
async def upsert_library(_, body: MediaLibUpsert) -> HTTPResponse:
    """Create or update a media library."""
    lib = await MediaLibService.upsert(body)
    return json(await MediaLibService.dump(lib))


@media.post("/lib/delete")
@authorize(role=UserRole.ADMIN)
@validate(json=IDs)
async def delete_libraries(_, body: IDs) -> HTTPResponse:
    """Delete the media libraries."""
    for id in body.ids:
        try:
            await MediaLibService.delete(int(id))
        except Exception:
            if len(body.ids) == 1:
                raise
            logger.error("Failed to delete the media library: %s", id, exc_info=True)
    return empty()


@media.get("/lib/<id:int>/scan")
async def scan_library(request: Request, id: int) -> HTTPResponse:
    """Scan the media library."""
    lib = await MediaLib.get(id=id)
    watcher: LibWatcher = request.app.ctx.lib_watcher
    await watcher.scan_directory(lib, validate_request=True)
    return empty()


@media.get("/list")
@validate(query=MediaQuery)
async def list_items(request: Request, query: MediaQuery) -> HTTPResponse:
    """List the media items."""
    queries = [
        # only list the top-level items if no path is specified
        Q(path=query.path) if query.path else Q(visible=True, parent_id__isnull=True)
    ]
    if query.lib_id:
        queries.append(Q(lib_id=query.lib_id))
    if query.keyword:
        queries.append(Q(keyword__icontains=query.keyword))
    page = await MediaItem.page(
        *queries,
        **query.page_params,
        annotations={"keyword": RawSQL("IFNULL(title, name)")},
    )
    summaries = await media_technical_summaries(page.items)
    result = await MediaItemService.dump_page(
        page, exclude={"lib", "parent", "children"}
    )
    for item in result["items"]:
        item.update(summaries.get(item["id"], {}))
    user: UserInfo = request.ctx.user
    targets = [("media", str(item["id"])) for item in result["items"]]
    ratings = await resource_rating_values(user.id, targets)
    for item in result["items"]:
        item["ratings"] = ratings.get(("media", str(item["id"])), [])
    return json(result)


async def random_feed_item(filters: list[Q]) -> MediaItem | None:
    items = (
        await MediaItem.filter(*filters, visible=True, size__isnull=False)
        .annotate(random_order=RawSQL("RANDOM()"))
        .order_by("random_order")
        .select_related("lib", "parent")
        .limit(25)
    )
    return next((item for item in items if Path(item.path).is_file()), None)


@media.get("/feed")
@authorize()
@validate(query=MediaFeedQuery)
async def get_random_feed_item(request: Request, query: MediaFeedQuery) -> HTTPResponse:
    """Return a random playable item from the user's permitted libraries."""
    filters = []
    user: UserInfo = request.ctx.user
    if user.perms is not None:
        filters.append(Q(lib_id__in=user.perms.media_lib_ids))
    excluded = feed_excluded_ids(query.exclude)
    if excluded:
        filters.append(~Q(id__in=excluded))

    item = await random_feed_item(filters)
    if not item and excluded:
        filters.pop()
        item = await random_feed_item(filters)
    if not item:
        return json(None)

    parent = item.parent
    return json(
        {
            "id": item.id,
            "lib_id": item.lib_id,
            "lib_type": item.lib.lib_type,
            "path": item.path,
            "name": item.name,
            "title": item.title,
            "poster": item.poster,
            "backdrop": item.backdrop,
            "season": item.season,
            "episode": item.episode,
            "parent_id": item.parent_id,
            "parent_name": (parent.title or parent.name) if parent else None,
        }
    )


@media.post("/delete")
@authorize(role=UserRole.ADMIN)
@validate(json=MediaDel)
async def delete_items(_, body: MediaDel) -> HTTPResponse:
    """Delete the media items."""
    for id in body.ids:
        try:
            await MediaItemService.delete(int(id), body.local)
        except Exception:
            if len(body.ids) == 1:
                raise
            logger.error("Failed to delete the media item: %s", id, exc_info=True)
    return empty()


@media.post("/<id:int>/rename")
@authorize(role=UserRole.ADMIN)
@validate(json=ResourceRename)
async def rename_item(_, body: ResourceRename, id: int) -> HTTPResponse:
    item = await MediaItemService.rename(id, body.name)
    return json(await MediaItemService.dump(item))


@media.post("/<id:int>/tags")
@authorize(role=UserRole.ADMIN)
@validate(json=MediaTags)
async def set_item_tags(_, body: MediaTags, id: int) -> HTTPResponse:
    item = await MediaItemService.set_tags(id, body.tags)
    return json(await MediaItemService.dump(item))


@media.get("/cover/<id:int>")
@authorize()
async def get_cover(request: Request, id: int) -> HTTPResponse | ResponseStream:
    """Generate a missing first-frame poster and return its cached file."""
    filters = [Q(id=id)]
    user: UserInfo = request.ctx.user
    if user.perms is not None:
        filters.append(Q(lib_id__in=user.perms.media_lib_ids))
    item = await MediaItem.filter(*filters).get_or_none()
    if not item:
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED)

    await ensure_default_thumbnail(item)
    if not item.poster:
        raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS)

    image_root = Path(KaloscopeConfig.get_workspace("images")).resolve()
    path = (image_root / item.poster).resolve()
    if not path.is_relative_to(image_root) or not path.is_file():
        raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS)
    content_type, _ = mimetypes.guess_file_type(path)
    return await file_stream(
        path,
        mime_type=content_type or "application/octet-stream",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@media.get("/<id:int>")
async def get_item_details(_, id: int) -> HTTPResponse:
    """Get the details of the media item."""
    media_item = await MediaItem.get(id=id)
    await ensure_default_thumbnail(media_item)
    children = await MediaItem.filter(parent_id=media_item.id, visible=True)
    sources = children if children else [media_item]
    if children:
        MediaItemService.request_technical_backfill(children)
    else:
        await MediaItemService.ensure_technical_metadata(media_item)

    item = await MediaItemService.dump(media_item)
    summary = MediaItemService.technical_summary(sources)
    summary["episode_count"] = (
        len(children)
        if item.get("lib", {}).get("lib_type") == LibType.TV_SHOW
        else None
    )
    item.update(summary)
    item["technical_pending"] = any(source.duration is None for source in sources)
    child_by_id = {child.id: child for child in children}
    for child in item.get("children") or []:
        source = child_by_id.get(child["id"])
        if source:
            child.update(MediaItemService.technical_summary([source]))
    if lib := item.get("lib"):
        # attach the triggers
        lib["triggers"] = await FlowTriggerService.get_triggers(
            GraphCategory.INGEST, lib["id"]
        )
        # attach the metadata
        if nfo_path := item.get("nfo_path"):
            item["metadata"] = parse_nfo(lib["lib_type"], nfo_path)
    return json(item)


@media.post("/<id:int>/gen_nfo")
@authorize(role=UserRole.ADMIN)
@validate(json=MediaMetadata)
async def generate_nfo(_, body: MediaMetadata, id: int) -> HTTPResponse:
    """Generate the NFO file for the media item."""
    item = await MediaItem.get_or_none(
        id=id,
        parent_id__isnull=True,
    ).select_related("lib")
    if not item:
        raise BadRequestException
    # overwrite the NFO file and update the metadata immediately
    lib = item.lib
    nfo_type = get_nfo_type(lib.lib_type)
    nfo_path = item.nfo_path or get_nfo_path(item.path)
    if await gen_nfo(nfo_type, nfo_path, body.metadata, overwrite=True):
        await update_metadata(lib, nfo_path, fallback=body.metadata)
    # also update the metadata of the child episodes if it's a TV show
    if lib.lib_type == LibType.TV_SHOW:
        await MediaItemService.refresh_episodes(item, body)
    return empty()


@media.post("/<id:int>/metadata")
@authorize(role=UserRole.ADMIN)
@validate(form=MediaMetadataEdit)
async def edit_metadata(_, body: MediaMetadataEdit, id: int) -> HTTPResponse:
    """Edit a movie, TV show, or episode's description and thumbnail."""
    item = await MediaItem.get_or_none(id=id).select_related("lib")
    if not item:
        raise BadRequestException

    old_poster = item.poster
    poster = body.poster.strip() or None
    uploaded_poster = None
    if body.thumbnail:
        uploaded_poster = await save_custom_thumbnail(body.thumbnail)
        poster = uploaded_poster
    elif body.frame is not None:
        uploaded_poster = await save_video_frame(item.path, body.frame)
        poster = uploaded_poster

    lib = item.lib
    nfo_type = NFOType.EPISODE if item.parent_id else get_nfo_type(lib.lib_type)
    nfo_path = item.nfo_path or get_nfo_path(item.path)
    try:
        await edit_nfo(
            nfo_type,
            nfo_path,
            title=item.title or item.name,
            plot=body.plot.strip() or None,
            poster=poster,
        )
        await update_metadata(lib, nfo_path)
    except Exception:
        await delete_custom_thumbnail(uploaded_poster)
        raise
    if old_poster != poster:
        await delete_custom_thumbnail(old_poster)
    return empty()


@media.get("/<id:int>/frames")
@authorize()
@validate(query=MediaFramesQuery)
async def list_frame_options(_, query: MediaFramesQuery, id: int) -> HTTPResponse:
    """Return evenly distributed candidate positions for a media file."""
    item = await MediaItem.get_or_none(id=id)
    if not item or not Path(item.path).is_file():
        raise BadRequestException
    duration = (await probe_media(item.path)).duration or 0
    return json({"positions": frame_positions(duration, query.count)})


@media.get("/<id:int>/frame")
@authorize()
@validate(query=MediaFrameQuery)
async def get_video_frame(_, query: MediaFrameQuery, id: int) -> HTTPResponse:
    """Extract one candidate frame without persisting it."""
    item = await MediaItem.get_or_none(id=id)
    if not item or not Path(item.path).is_file():
        raise BadRequestException
    data = await extract_video_frame(item.path, query.position)
    return raw(data, content_type="image/jpeg", headers={"Cache-Control": "no-store"})


async def permitted_media_item(request: Request, id: int) -> MediaItem:
    filters = [Q(id=id)]
    user: UserInfo = request.ctx.user
    if user.perms is not None:
        filters.append(Q(lib_id__in=user.perms.media_lib_ids))
    item = await MediaItem.filter(*filters).get_or_none()
    if not item:
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED)
    return item


@media.get("/<id:int>/screenshots")
@authorize()
async def list_screenshots(request: Request, id: int) -> HTTPResponse:
    """Return cached screenshots and enqueue missing frames in the background."""
    item = await permitted_media_item(request, id)
    return json(await request_screenshots(item))


@media.get("/<id:int>/screenshot/<fingerprint:str>/<index:int>")
@authorize()
async def get_screenshot(
    request: Request, id: int, fingerprint: str, index: int
) -> HTTPResponse | ResponseStream:
    """Serve one screenshot from the current source/config cache."""
    if not re.fullmatch(r"[0-9a-f]{16}", fingerprint):
        raise BadRequestException
    item = await permitted_media_item(request, id)
    path = await current_screenshot_path(item, fingerprint, index)
    if not path:
        raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS)
    return await file_stream(
        path,
        mime_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@media.get("/title")
@validate(query=MediaResource)
async def get_item_title(_, query: MediaResource) -> HTTPResponse:
    """Extract a scrape title from the media resource path."""
    path = Path(query.path)
    return json({"title": extract_title(path.name if path.is_dir() else path.stem)})


@media.get("/probe")
@validate(query=MediaResource)
async def probe_media_metadata(_, query: MediaResource) -> HTTPResponse:
    """Probe media duration and embedded chapters via ffprobe."""
    path = query.path
    if not await MediaItem.filter(path=path).exists():
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED)
    metadata = await probe_media(path)
    return json(
        {
            "duration": metadata.duration or 0,
            "chapters": [
                {
                    "id": chapter.id,
                    "title": chapter.title,
                    "start": chapter.start,
                    "end": chapter.end,
                }
                for chapter in metadata.chapters
            ],
        }
    )


@media.get("/stream")
@validate(query=TranscodeQuery)
async def get_item_stream(
    request: Request, query: TranscodeQuery
) -> HTTPResponse | ResponseStream:
    """Get the media item stream with optional real-time ffmpeg transcoding."""
    path = query.path
    if not await MediaItem.filter(path=path).exists():
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED)
    if not await async_os.path.exists(path):
        raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS)

    # -------------------- Transcoding with ffmpeg and HLS --------------------
    if query.transcode:
        options = await query.options()

        # resolve the media hash
        media_hash = await MediaItemService.resolve_media_hash(path)

        # start or wait for the transcoding process to produce the M3U8 output
        media_hash, profile = await ensure_transcode(path, media_hash, options)

        # redirect to the deterministic M3U8 path
        return redirect(f"/_api/media/hls/{media_hash}/{profile}/index.m3u8")

    # -------------------- Direct file streaming --------------------
    stat = await async_os.stat(path)
    total = stat.st_size
    headers = {"Accept-Ranges": "bytes"}

    # get the range header from the request
    range = request.headers.get("Range")
    if range:
        # parse the range header
        match = re.match(r"bytes=(\d*)-(\d*)", range)
        if not match:
            raise InvalidRangeType

        start, end = match.groups()
        start = int(start) if start else 0
        end = int(end) if end else total - 1

        # validate range
        if start >= total or end >= total or start > end:
            raise RangeNotSatisfiable

        # stream the requested range
        return await file_stream(
            path,
            headers=headers,
            _range=Range(start=start, end=end, size=end - start + 1, total=total),
        )

    # if no range header, return the entire file
    return await file_stream(path, headers=headers)


@media.get("/hls/<hash>/<profile>/<filename:ext=m3u8|ts>")
async def serve_hls_file(
    _, hash: str, profile: str, filename: str, ext: str
) -> HTTPResponse | ResponseStream:
    """Serve any file from an HLS output directory (M3U8 playlist or TS segment)."""
    file_path = (output_dir(hash, profile) / f"{filename}.{ext}").resolve()
    transcoded = Path(KaloscopeConfig.get_workspace("transcoded")).resolve()
    if not file_path.is_relative_to(transcoded):
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED)
    if not file_path.is_file():
        raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS)

    # M3U8 playlist
    if ext == "m3u8":
        content = await read_m3u8(file_path)
        if content is None:
            raise BadRequestException("HLS output not found")
        return HTTPResponse(
            content,
            content_type="application/vnd.apple.mpegurl",
            headers={
                "Accept-Ranges": "none",
                "Cache-Control": "no-cache",
            },
        )

    # TS segment
    return await file_stream(
        file_path,
        headers={"Cache-Control": "no-store"},
    )


@media.get("/proxy")
@validate(query=RemoteProxy)
async def proxy_remote_media(
    request: Request, query: RemoteProxy
) -> HTTPResponse | ResponseStream:
    """Proxy a remote media stream from the given URL."""
    url, headers = remote_proxy_request(
        query.url, query.referer, query.ua, request.headers
    )
    client: AsyncSession = request.app.ctx.curl_cffi

    async def _stream(stream):
        response = None
        try:
            # Match the request engine used by protected workflow sources. Several
            # CDNs reject a plain HTTP client even when Referer and Range are valid.
            impersonation_headers = {
                "user-agent",
                "sec-ch-ua",
                "sec-ch-ua-mobile",
                "sec-ch-ua-platform",
            }
            response = await client.get(
                url,
                headers=[
                    (key, value)
                    for key, value in headers.items()
                    if key.lower() not in impersonation_headers
                ],
                proxy=await resolve_proxy(url),
                impersonate="chrome",
                stream=True,
            )
            stream.response.status = response.status_code
            content_type = (
                response.headers.get("content-type", "").split(";", 1)[0].lower()
            )
            is_hls = (
                content_type in HLS_CONTENT_TYPES
                or ".m3u8" in str(response.url).lower()
            )
            # copy the response headers to the stream response
            for header in PROXY_RESPONSE_HEADERS:
                if header == "content-disposition":
                    continue
                if is_hls and header in {"content-encoding", "content-length"}:
                    continue
                if value := response.headers.get(header):
                    stream.response.headers[header.title()] = value
            if is_hls:
                stream.response.headers["Content-Type"] = (
                    "application/vnd.apple.mpegurl"
                )
                content = (await response.acontent()).decode(
                    "utf-8", errors="replace"
                )
                rewritten = rewrite_hls_playlist(
                    content, str(response.url), query.referer, query.ua
                )
                await stream.write(rewritten.encode())
                return
            # Attachment-only links are valid media sources once proxied inline.
            stream.response.headers["Content-Disposition"] = "inline"
            async for chunk in response.aiter_content():
                await stream.write(chunk)
        except (httpx.RequestError, RequestException) as e:
            logger.error(
                "An error occurred while proxying remote media %s.",
                url,
                exc_info=True,
            )
            raise KaloscopeException(ErrorCode.HTTP_REQUEST_FAILED) from e
        finally:
            if response is not None:
                await response.aclose()

    return ResponseStream(_stream)


@media.get("/transcode/list")
@validate(query=TranscodeTaskQuery)
async def list_transcodes(_, query: TranscodeTaskQuery) -> HTTPResponse:
    """List in-memory and finished transcode tasks."""
    tasks = await list_tasks()

    # attach media item info to tasks if available
    hashes = {task["hash"] for task in tasks if task.get("hash")}
    if hashes:
        items = await MediaItem.filter(hash__in=hashes).values(
            "hash",
            "name",
            "title",
            "path",
            "season",
            "episode",
            parent_name="parent__name",
            parent_title="parent__title",
        )
        hash_items = {}
        for item in items:
            hash_items.setdefault(item["hash"], item)
        for task in tasks:
            if item := hash_items.get(task["hash"]):
                title = item["title"] or item["name"]
                if item["season"] is not None and item["episode"] is not None:
                    title = f"S{item['season']}E{item['episode']} - {title}"
                parent = item["parent_title"] or item["parent_name"]
                task["title"] = parent or title
                if parent:
                    task["subtitle"] = title
                task["path"] = item["path"]

    # filter tasks by state and keyword
    if query.state:
        tasks = [task for task in tasks if task["state"] == query.state]
    if query.keyword:
        keyword = query.keyword.lower()
        tasks = [
            task
            for task in tasks
            if any(
                keyword in value.lower()
                for value in (
                    task.get("title") or task["name"],
                    task.get("subtitle"),
                )
                if value
            )
        ]

    # sort tasks by ordering field
    if query.ordering:
        reverse = query.ordering.startswith("-")
        field = query.ordering[1:] if reverse else query.ordering
        tasks.sort(
            key=lambda task: (task.get(field) is None, task.get(field)),
            reverse=reverse,
        )

    return json(tasks)


@media.post("/transcode/stop")
@authorize(role=UserRole.ADMIN)
@validate(json=IDs)
async def stop_transcodes(_, body: IDs) -> HTTPResponse:
    """Stop running transcode tasks by ID."""
    ids = await stop_tasks([str(id) for id in body.ids])
    return json({"ids": ids})


@media.post("/transcode/delete")
@authorize(role=UserRole.ADMIN)
@validate(json=IDs)
async def delete_transcodes(_, body: IDs) -> HTTPResponse:
    """Delete non-running transcode outputs by ID."""
    ids = await delete_tasks([str(id) for id in body.ids])
    return json({"ids": ids})
