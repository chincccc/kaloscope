from sanic import Blueprint, HTTPResponse, empty, json
from sanic_ext import validate
from tortoise.expressions import Q

from app.core.decorators import authorize
from app.core.exceptions import BadRequestException
from app.core.media.thumbnails import (
    THUMBNAIL_SOURCE_CONFIG_KEY,
    THUMBNAIL_SOURCE_FIRST,
    THUMBNAIL_SOURCE_OPTIONS,
    invalidate_auto_thumbnails,
)
from app.models.base import IDs
from app.models.general import ConfigQuery, ConfigUpsert, GlobalConfig
from app.models.user import UserRole
from app.services.config import ConfigService

config = Blueprint("config", url_prefix="/config")


@config.get("/list")
@validate(query=ConfigQuery)
async def list_configs(_, query: ConfigQuery) -> HTTPResponse:
    """List the global configs."""
    queries = []
    if query.key:
        queries.append(Q(key__icontains=query.key))
    page = await GlobalConfig.page(*queries, **query.page_params)
    return json(
        {
            "total": page.total,
            "items": [ConfigService.dump(c) for c in page.items],
        }
    )


@config.get("/<key>")
async def get_config(_, key: str) -> HTTPResponse:
    """Get a single global config by key."""
    config = await GlobalConfig.get_or_none(key=key)
    return json(config.value if config else None)


@config.post("/upsert")
@authorize(role=UserRole.ADMIN)
@validate(json=ConfigUpsert)
async def upsert_config(_, body: ConfigUpsert) -> HTTPResponse:
    """Create or update a global config."""
    if body.key == "media.screenshot_count":
        if isinstance(body.value, bool):
            raise BadRequestException
        try:
            value = int(body.value)
        except (TypeError, ValueError):
            raise BadRequestException from None
        if value != body.value or not 0 <= value <= 24:
            raise BadRequestException
        body.value = value

    previous_thumbnail_source = THUMBNAIL_SOURCE_FIRST
    if body.key == THUMBNAIL_SOURCE_CONFIG_KEY:
        if body.value not in THUMBNAIL_SOURCE_OPTIONS:
            raise BadRequestException
        previous = await GlobalConfig.get_or_none(key=THUMBNAIL_SOURCE_CONFIG_KEY)
        if previous and previous.value in THUMBNAIL_SOURCE_OPTIONS:
            previous_thumbnail_source = previous.value

    config = await ConfigService.upsert(body)
    if (
        body.key == THUMBNAIL_SOURCE_CONFIG_KEY
        and body.value != previous_thumbnail_source
    ):
        await invalidate_auto_thumbnails()
    return json(ConfigService.dump(config))


@config.post("/delete")
@authorize(role=UserRole.ADMIN)
@validate(json=IDs)
async def delete_configs(_, body: IDs) -> HTTPResponse:
    """Delete the global configs."""
    await GlobalConfig.filter(id__in=body.ids).delete()
    return empty()
