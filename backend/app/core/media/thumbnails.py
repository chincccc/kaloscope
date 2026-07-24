import asyncio
import random
from pathlib import Path

from lxml import etree

from app.core.media.editor import delete_custom_thumbnail
from app.models.general import GlobalConfig
from app.models.media import MediaItem

THUMBNAIL_SOURCE_CONFIG_KEY = "media.thumbnail_source"
THUMBNAIL_SOURCE_FIRST = "first"
THUMBNAIL_SOURCE_MIDDLE = "middle"
THUMBNAIL_SOURCE_RANDOM = "random"
THUMBNAIL_SOURCE_OPTIONS = frozenset(
    {
        THUMBNAIL_SOURCE_FIRST,
        THUMBNAIL_SOURCE_MIDDLE,
        THUMBNAIL_SOURCE_RANDOM,
    }
)

POSTER_SOURCE_AUTO = "auto"
POSTER_SOURCE_CUSTOM = "custom"


async def thumbnail_source() -> str:
    config = await GlobalConfig.get_or_none(key=THUMBNAIL_SOURCE_CONFIG_KEY)
    value = config.value if config else THUMBNAIL_SOURCE_FIRST
    return value if value in THUMBNAIL_SOURCE_OPTIONS else THUMBNAIL_SOURCE_FIRST


def thumbnail_position(source: str, duration: float | None) -> float:
    if source == THUMBNAIL_SOURCE_FIRST or not duration or duration <= 0:
        return 0.0
    if source == THUMBNAIL_SOURCE_MIDDLE:
        return round(duration / 2, 3)
    if source == THUMBNAIL_SOURCE_RANDOM:
        return round(duration * random.random(), 3)
    return 0.0


def nfo_references_poster(nfo_path: str | None, poster: str) -> bool:
    if not nfo_path or not Path(nfo_path).is_file():
        return False
    try:
        tree = etree.parse(nfo_path, parser=etree.XMLParser(recover=True))
        return poster in {
            value.strip()
            for value in tree.xpath("//art/poster/text()")
            if isinstance(value, str) and value.strip()
        }
    except (OSError, etree.XMLSyntaxError):
        return False


async def classify_unmarked_poster(item: MediaItem) -> str | None:
    """Identify a legacy poster without treating generated files as custom."""
    if item.poster_source or not item.poster:
        return item.poster_source
    poster = item.poster
    is_auto = poster.startswith("media/") and not await asyncio.to_thread(
        nfo_references_poster, item.nfo_path, poster
    )
    source = POSTER_SOURCE_AUTO if is_auto else POSTER_SOURCE_CUSTOM
    updated = await MediaItem.filter(id=item.id, poster_source__isnull=True).update(
        poster_source=source
    )
    if updated:
        item.poster_source = source
    return source


async def classify_unmarked_posters():
    items = await MediaItem.filter(
        poster_source__isnull=True,
        poster__not_isnull=True,
    )
    for item in items:
        await classify_unmarked_poster(item)


async def invalidate_auto_thumbnails():
    """Remove only generated posters so they are lazily rebuilt with new settings."""
    await classify_unmarked_posters()
    posters = set(
        await MediaItem.filter(poster_source=POSTER_SOURCE_AUTO).values_list(
            "poster", flat=True
        )
    )
    await MediaItem.filter(poster_source=POSTER_SOURCE_AUTO).update(
        poster=None,
        poster_source=None,
    )
    for poster in posters:
        await delete_custom_thumbnail(poster)
