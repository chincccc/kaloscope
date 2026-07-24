from pathlib import Path

from sanic import Blueprint, HTTPResponse, Request, json
from sanic_ext import validate
from tortoise.expressions import Q

from app.core.decorators import authorize
from app.core.media.filename_tags import filename_tags
from app.models.gallery import Gallery, GalleryItem
from app.models.media import MediaItem
from app.models.search import UnifiedSearchQuery, UnifiedSearchType
from app.models.user import UserInfo
from app.services.gallery import gallery_book_key, natural_sort_key
from app.services.rating import resource_rating_values

search = Blueprint("search", url_prefix="/search")


def _page(items: list[dict], page_num: int, page_size: int) -> list[dict]:
    if page_num <= 0:
        return items
    start = (page_num - 1) * page_size
    return items[start : start + page_size]


def _matches_rating_filters(values: list[dict], filters: list[tuple[str, int]]) -> bool:
    scores = {value["key"]: value["score"] for value in values}
    return all(scores.get(dimension, 0) >= minimum for dimension, minimum in filters)


def _media_result(item: MediaItem, lib=None) -> dict:
    library = lib or item.lib
    return {
        "id": item.id,
        "lib_id": item.lib_id,
        "lib_name": library.name,
        "lib_type": library.lib_type,
        "name": item.title or item.name,
        "poster": item.poster,
        "year": item.year,
        "tags": filename_tags(Path(item.path).name),
    }


@search.get("")
@authorize()
@validate(query=UnifiedSearchQuery)
async def unified_search(request: Request, query: UnifiedSearchQuery) -> HTTPResponse:
    """Search all permitted local media and gallery content."""
    user: UserInfo = request.ctx.user
    selected_types = query.selected_types
    media_filters = [Q(visible=True)]
    gallery_filters = []
    if user.perms is not None:
        media_filters.append(Q(lib_id__in=user.perms.media_lib_ids))
        gallery_filters.append(Q(id__in=user.perms.gallery_ids))

    keyword_filter = Q(title__icontains=query.keyword) | Q(
        name__icontains=query.keyword
    )
    movies_by_id: dict[int, dict] = {}
    tv_shows_by_id: dict[int, dict] = {}
    episodes = []
    media_types = {
        UnifiedSearchType.MOVIE,
        UnifiedSearchType.TV_SHOW,
        UnifiedSearchType.EPISODE,
    }
    if selected_types & media_types:
        media_items = await MediaItem.filter(
            *media_filters, keyword_filter
        ).select_related("lib", "parent")
        for item in media_items:
            if item.parent_id is None:
                item_type = UnifiedSearchType(item.lib.lib_type.value)
                if item_type is UnifiedSearchType.MOVIE and item_type in selected_types:
                    movies_by_id[item.id] = _media_result(item)
                elif (
                    item_type is UnifiedSearchType.TV_SHOW
                    and item_type in selected_types
                ):
                    tv_shows_by_id[item.id] = _media_result(item)
                continue
            if UnifiedSearchType.EPISODE not in selected_types:
                continue
            result = _media_result(item)
            result.update(
                {
                    "parent_id": item.parent_id,
                    "parent_name": item.parent.title or item.parent.name,
                    "poster": item.poster or item.parent.poster,
                    "season": item.season,
                    "episode": item.episode,
                }
            )
            episodes.append(result)
            if UnifiedSearchType.TV_SHOW in selected_types and item.parent is not None:
                tv_shows_by_id.setdefault(
                    item.parent_id, _media_result(item.parent, item.lib)
                )

    gallery_types = {UnifiedSearchType.GALLERY_BOOK, UnifiedSearchType.IMAGE}
    libraries = (
        await Gallery.filter(*gallery_filters) if selected_types & gallery_types else []
    )
    library_map = {library.id: library for library in libraries}
    gallery_items = (
        await GalleryItem.filter(gallery_id__in=library_map).order_by("name")
        if library_map
        else []
    )
    folded_keyword = query.keyword.casefold()
    book_groups: dict[tuple[int, str | None], list[GalleryItem]] = {}
    image_matched_books: set[tuple[int, str | None]] = set()
    images = []
    for item in gallery_items:
        library = library_map[item.gallery_id]
        book = gallery_book_key(library.dir, item.dir)
        book_key = (library.id, book)
        book_groups.setdefault(book_key, []).append(item)
        image_matches = folded_keyword in item.name.casefold()
        if UnifiedSearchType.IMAGE in selected_types and image_matches:
            image_matched_books.add(book_key)
            images.append(
                {
                    "id": item.id,
                    "gallery_id": library.id,
                    "gallery_name": library.name,
                    "book_name": book,
                    "name": item.name,
                    "tags": filename_tags(book or ""),
                }
            )

    books = []
    for (gallery_id, name), items in book_groups.items():
        display_name = name or ""
        folder_matches = folded_keyword in display_name.casefold()
        child_matches = (gallery_id, name) in image_matched_books
        if UnifiedSearchType.GALLERY_BOOK not in selected_types or not (
            folder_matches or child_matches
        ):
            continue
        library = library_map[gallery_id]
        items.sort(key=lambda item: natural_sort_key(item.name))
        books.append(
            {
                "id": items[0].id,
                "gallery_id": gallery_id,
                "gallery_name": library.name,
                "name": name,
                "tags": filename_tags(name or ""),
                "uncategorized": name is None,
                "item_count": len(items),
            }
        )

    movies = list(movies_by_id.values())
    tv_shows = list(tv_shows_by_id.values())
    top_level = movies + tv_shows
    rating_targets = [("media", str(item["id"])) for item in top_level] + [
        ("gallery_book", f"{item['gallery_id']}:{item['name'] or ''}") for item in books
    ]
    ratings = await resource_rating_values(user.id, rating_targets)
    rating_filters = query.selected_rating_filters
    if rating_filters:

        def matches_rating(target: tuple[str, str]) -> bool:
            return _matches_rating_filters(ratings.get(target, []), rating_filters)

        movies = [item for item in movies if matches_rating(("media", str(item["id"])))]
        tv_shows = [
            item for item in tv_shows if matches_rating(("media", str(item["id"])))
        ]
        books = [
            item
            for item in books
            if matches_rating(
                ("gallery_book", f"{item['gallery_id']}:{item['name'] or ''}")
            )
        ]
        episodes = []
        images = []

    for item in movies + tv_shows:
        item["ratings"] = ratings.get(("media", str(item["id"])), [])
    for item in books:
        item["ratings"] = ratings.get(
            ("gallery_book", f"{item['gallery_id']}:{item['name'] or ''}"), []
        )

    movies.sort(key=lambda item: natural_sort_key(item["name"]))
    tv_shows.sort(key=lambda item: natural_sort_key(item["name"]))
    episodes.sort(key=lambda item: natural_sort_key(item["name"]))
    books.sort(key=lambda item: natural_sort_key(item["name"] or ""))
    images.sort(key=lambda item: natural_sort_key(item["name"]))
    groups = {
        "movies": movies,
        "tv_shows": tv_shows,
        "episodes": episodes,
        "books": books,
        "images": images,
    }
    return json(
        {
            **{
                key: _page(items, query.page_num, query.page_size)
                for key, items in groups.items()
            },
            "totals": {key: len(items) for key, items in groups.items()},
        }
    )
