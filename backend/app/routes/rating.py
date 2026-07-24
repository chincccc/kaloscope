from sanic import Blueprint, HTTPResponse, Request, empty, json
from sanic_ext import validate

from app.core.decorators import authorize
from app.core.exceptions import BadRequestException, ErrorCode, ForbiddenException
from app.models.gallery import GalleryItem
from app.models.media import MediaItem
from app.models.rating import (
    RatingDimensionCreate,
    RatingResourceType,
    RatingUpdate,
    ResourceRating,
    gallery_rating_key,
    media_rating_key,
)
from app.models.user import UserInfo, UserRole
from app.services.gallery import gallery_book_key
from app.services.rating import (
    DEFAULT_DIMENSION,
    add_custom_dimension,
    rating_dimensions,
    remove_custom_dimension,
)

rating = Blueprint("rating", url_prefix="/rating")


async def _target_key(
    request: Request, resource_type: str, resource_id: int
) -> tuple[RatingResourceType, str]:
    user: UserInfo = request.ctx.user
    try:
        kind = RatingResourceType(resource_type)
    except ValueError as exc:
        raise BadRequestException() from exc

    if kind == RatingResourceType.MEDIA:
        item = await MediaItem.get_or_none(id=resource_id).select_related("lib")
        if item is None or item.parent_id is not None:
            raise BadRequestException()
        if user.perms is not None and item.lib_id not in user.perms.media_lib_ids:
            raise ForbiddenException(ErrorCode.PERMISSION_DENIED)
        return kind, media_rating_key(item.id)

    item = await GalleryItem.get_or_none(id=resource_id).select_related("gallery")
    if item is None:
        raise BadRequestException()
    if user.perms is not None and item.gallery_id not in user.perms.gallery_ids:
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED)
    book_key = gallery_book_key(item.gallery.dir, item.dir)
    return kind, gallery_rating_key(item.gallery_id, book_key)


@rating.get("/dimensions")
@authorize()
async def list_dimensions(request: Request) -> HTTPResponse:
    user: UserInfo = request.ctx.user
    return json(await rating_dimensions(user.id))


@rating.post("/dimensions")
@authorize()
@validate(json=RatingDimensionCreate)
async def create_dimension(
    request: Request, body: RatingDimensionCreate
) -> HTTPResponse:
    user: UserInfo = request.ctx.user
    try:
        result = await add_custom_dimension(user.id, body.name)
    except ValueError as exc:
        raise BadRequestException() from exc
    return json(result)


@rating.delete("/dimensions/<key:str>")
@authorize()
async def delete_dimension(request: Request, key: str) -> HTTPResponse:
    if key == DEFAULT_DIMENSION["key"]:
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED)
    user: UserInfo = request.ctx.user
    if not await remove_custom_dimension(user.id, key):
        raise BadRequestException()
    return empty()


@rating.get("/<resource_type:str>/<resource_id:int>")
@authorize()
async def get_ratings(
    request: Request, resource_type: str, resource_id: int
) -> HTTPResponse:
    user: UserInfo = request.ctx.user
    kind, key = await _target_key(request, resource_type, resource_id)
    dimensions = await rating_dimensions(user.id)
    custom_keys = [item["key"] for item in dimensions if item["removable"]]
    rows = await ResourceRating.filter(
        resource_type=kind.value,
        resource_key=key,
        dimension_key__in=[DEFAULT_DIMENSION["key"], *custom_keys],
        scope_user_id__in=[0, user.id],
    )
    values = {
        row.dimension_key: row.score
        for row in rows
        if (row.dimension_key == DEFAULT_DIMENSION["key"] and row.scope_user_id == 0)
        or (row.dimension_key in custom_keys and row.scope_user_id == user.id)
    }
    return json(
        {
            "dimensions": [
                {
                    **dimension,
                    "score": values.get(dimension["key"]),
                    "editable": dimension["removable"] or user.role == UserRole.ADMIN,
                }
                for dimension in dimensions
            ]
        }
    )


@rating.post("/<resource_type:str>/<resource_id:int>")
@authorize()
@validate(json=RatingUpdate)
async def set_rating(
    request: Request,
    body: RatingUpdate,
    resource_type: str,
    resource_id: int,
) -> HTTPResponse:
    user: UserInfo = request.ctx.user
    kind, key = await _target_key(request, resource_type, resource_id)
    dimensions = await rating_dimensions(user.id)
    dimension_keys = {item["key"] for item in dimensions}
    if body.dimension_key not in dimension_keys:
        raise BadRequestException()
    if body.dimension_key == DEFAULT_DIMENSION["key"]:
        if user.role != UserRole.ADMIN:
            raise ForbiddenException(ErrorCode.PERMISSION_DENIED)
        scope_user_id = 0
    else:
        scope_user_id = user.id

    filters = {
        "scope_user_id": scope_user_id,
        "resource_type": kind.value,
        "resource_key": key,
        "dimension_key": body.dimension_key,
    }
    if body.score is None:
        await ResourceRating.filter(**filters).delete()
    else:
        await ResourceRating.update_or_create(**filters, defaults={"score": body.score})
    return json({"dimension_key": body.dimension_key, "score": body.score})
