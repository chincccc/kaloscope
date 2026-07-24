import mimetypes
from pathlib import Path

from sanic import Blueprint, HTTPResponse, Request, empty, json
from sanic.log import logger
from sanic.response import ResponseStream, file_stream
from sanic_ext import validate
from tortoise.expressions import Q

from app.core.decorators import authorize
from app.core.exceptions import ErrorCode, ForbiddenException
from app.core.gallery_archive import resolve_gallery_image
from app.core.gallery_thumbnail import ensure_gallery_thumbnail
from app.models.base import IDs, ResourceRename
from app.models.gallery import Gallery, GalleryItem, GalleryQuery, GalleryUpsert
from app.models.media import MediaTags
from app.models.user import UserInfo, UserRole
from app.services.gallery import (
    GalleryItemService,
    GalleryService,
    chapter_directory_sort_key,
    gallery_book_key,
    natural_sort_key,
    relative_parts,
)
from app.services.rating import resource_rating_values

gallery = Blueprint("gallery", url_prefix="/gallery")


def permitted_item_filters(request: Request, id: int) -> list[Q]:
    filters = [Q(id=id)]
    user: UserInfo = request.ctx.user
    if user.perms is not None:
        filters.append(Q(gallery_id__in=user.perms.gallery_ids))
    return filters


@gallery.get("/lib/list")
@authorize()
async def list_galleries(request: Request) -> HTTPResponse:
    queries = []
    user: UserInfo = request.ctx.user
    if user.perms is not None:
        queries.append(Q(id__in=user.perms.gallery_ids))
    galleries = await GalleryService.dump_list(Gallery.filter(*queries))
    for item in galleries:
        item["item_count"] = await GalleryItem.filter(gallery_id=item["id"]).count()
        item["scanning"] = GalleryService.is_scanning(item["id"])
    return json(galleries)


@gallery.post("/lib/sort")
@authorize(role=UserRole.ADMIN)
@validate(json=IDs)
async def sort_galleries(_, body: IDs) -> HTTPResponse:
    await GalleryService.update_priorities(body.ids)
    return empty()


@gallery.post("/lib/upsert")
@authorize(role=UserRole.ADMIN)
@validate(json=GalleryUpsert)
async def upsert_gallery(_, body: GalleryUpsert) -> HTTPResponse:
    item = await GalleryService.upsert(body)
    return json(await GalleryService.dump(item))


@gallery.post("/lib/delete")
@authorize(role=UserRole.ADMIN)
@validate(json=IDs)
async def delete_galleries(_, body: IDs) -> HTTPResponse:
    for id in body.ids:
        try:
            await GalleryService.delete(int(id))
        except Exception:
            if len(body.ids) == 1:
                raise
            logger.error("Failed to delete gallery: %s", id, exc_info=True)
    return empty()


@gallery.get("/lib/<id:int>/scan")
@authorize(role=UserRole.ADMIN)
async def scan_gallery(_, id: int) -> HTTPResponse:
    GalleryService.request_scan(id)
    return empty()


@gallery.get("/list")
@authorize()
@validate(query=GalleryQuery)
async def list_items(request: Request, query: GalleryQuery) -> HTTPResponse:
    filters = []
    user: UserInfo = request.ctx.user
    if user.perms is not None:
        filters.append(Q(gallery_id__in=user.perms.gallery_ids))
    if query.gallery_id:
        filters.append(Q(gallery_id=query.gallery_id))
    if query.keyword:
        filters.append(Q(name__icontains=query.keyword))
    page = await GalleryItem.page(*filters, **query.page_params)
    return json(await GalleryItemService.dump_page(page))


@gallery.get("/book/list")
@authorize()
@validate(query=GalleryQuery)
async def list_books(request: Request, query: GalleryQuery) -> HTTPResponse:
    if not query.gallery_id:
        return json({"total": 0, "items": [], "scanning": False})

    gallery_filters = [Q(id=query.gallery_id)]
    user: UserInfo = request.ctx.user
    if user.perms is not None:
        gallery_filters.append(Q(id__in=user.perms.gallery_ids))
    library = await Gallery.filter(*gallery_filters).get_or_none()
    if not library:
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED)

    GalleryService.request_scan(library.id, min_interval=30)
    books = await GalleryService.book_index(library.id, library.dir)
    if query.keyword:
        keyword = query.keyword.casefold()
        books = [
            book
            for book in books
            if book["name"] is not None and keyword in book["name"].casefold()
        ]
    total = len(books)
    if query.page_num > 0:
        start = (query.page_num - 1) * query.page_size
        books = books[start : start + query.page_size]
    targets = [("gallery_book", f"{library.id}:{book['name'] or ''}") for book in books]
    ratings = await resource_rating_values(user.id, targets)
    for book in books:
        book["ratings"] = ratings.get(
            ("gallery_book", f"{library.id}:{book['name'] or ''}"), []
        )
    return json(
        {
            "total": total,
            "items": books,
            "scanning": GalleryService.is_scanning(library.id),
        }
    )


@gallery.post("/book/<id:int>/rename")
@authorize(role=UserRole.ADMIN)
@validate(json=ResourceRename)
async def rename_book(_, body: ResourceRename, id: int) -> HTTPResponse:
    return json(await GalleryService.rename_book(id, body.name))


@gallery.post("/book/<id:int>/tags")
@authorize(role=UserRole.ADMIN)
@validate(json=MediaTags)
async def set_book_tags(_, body: MediaTags, id: int) -> HTTPResponse:
    return json(await GalleryService.set_book_tags(id, body.tags))


@gallery.get("/reader/<id:int>")
@authorize()
async def get_reader_context(request: Request, id: int) -> HTTPResponse:
    selected = await GalleryItem.filter(
        *permitted_item_filters(request, id)
    ).get_or_none()
    if not selected:
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED)

    library = await Gallery.get(id=selected.gallery_id)
    values = await GalleryItem.filter(gallery_id=library.id).values("id", "dir", "name")
    book_key = gallery_book_key(library.dir, selected.dir)
    book_root = str(Path(library.dir, book_key)) if book_key else library.dir
    values = [
        item
        for item in values
        if gallery_book_key(library.dir, item["dir"]) == book_key
    ]
    grouped: dict[str, list[dict]] = {}
    for item in values:
        grouped.setdefault(item["dir"], []).append(item)

    chapters = []
    current_items = []
    for directory in sorted(
        grouped,
        key=lambda value: chapter_directory_sort_key(book_root, value),
    ):
        items = sorted(
            grouped[directory], key=lambda item: natural_sort_key(item["name"])
        )
        parts = relative_parts(book_root, directory)
        title = parts[-1] if parts else (book_key or "")
        volume = "/".join(parts[:-1]) or None
        chapters.append(
            {
                "id": str(items[0]["id"]),
                "title": title,
                "volume": volume,
                "unfiled": not parts,
            }
        )
        if directory == selected.dir:
            current_items = items

    current_index = next(
        (
            index
            for index, item in enumerate(current_items)
            if item["id"] == selected.id
        ),
        0,
    )
    chapter_id = str(current_items[0]["id"]) if current_items else str(selected.id)
    return json(
        {
            "title": book_key,
            "uncategorized": book_key is None,
            "chapter_id": chapter_id,
            "chapters": chapters,
            "items": current_items,
            "current_index": current_index,
        }
    )


@gallery.get("/<id:int>")
@authorize()
async def get_item(request: Request, id: int) -> HTTPResponse:
    item = await GalleryItem.filter(*permitted_item_filters(request, id)).get_or_none()
    if not item:
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED)
    return json(await GalleryItemService.dump(item))


@gallery.get("/image/<id:int>")
@authorize()
async def get_image(request: Request, id: int) -> HTTPResponse | ResponseStream:
    item = await GalleryItem.filter(*permitted_item_filters(request, id)).get_or_none()
    if not item:
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED)
    path = await resolve_gallery_image(item.path)
    content_type, _ = mimetypes.guess_file_type(item.name)
    return await file_stream(
        path,
        mime_type=content_type or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@gallery.get("/cover/<id:int>")
@authorize()
async def get_cover(request: Request, id: int) -> HTTPResponse | ResponseStream:
    item = await GalleryItem.filter(*permitted_item_filters(request, id)).get_or_none()
    if not item:
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED)
    source = await resolve_gallery_image(item.path)
    path = await ensure_gallery_thumbnail(item.id, source)
    return await file_stream(
        path,
        mime_type="image/webp",
        headers={"Cache-Control": "private, max-age=3600"},
    )
