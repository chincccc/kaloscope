from enum import StrEnum

from pydantic import BaseModel, Field, PositiveInt
from tortoise.fields import CharField, IntField

from app.models.base import TortoiseModel


class RatingResourceType(StrEnum):
    MEDIA = "media"
    GALLERY_BOOK = "gallery_book"


class ResourceRating(TortoiseModel):
    # 0 is the shared administrator rating; positive values are user IDs.
    scope_user_id = IntField(db_index=True)
    resource_type = CharField(max_length=16)
    resource_key = CharField(max_length=4096)
    dimension_key = CharField(max_length=32)
    score = IntField()

    class Meta:
        table = "resource_rating"
        unique_together = (
            ("scope_user_id", "resource_type", "resource_key", "dimension_key"),
        )


class RatingDimensionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=32)


class RatingUpdate(BaseModel):
    dimension_key: str = Field(min_length=1, max_length=32)
    score: int | None = Field(ge=1, le=10, default=None)


class RatingTarget(BaseModel):
    resource_type: RatingResourceType
    resource_id: PositiveInt


def media_rating_key(item_id: int) -> str:
    return str(item_id)


def gallery_rating_key(gallery_id: int, book_key: str | None) -> str:
    return f"{gallery_id}:{book_key or ''}"
