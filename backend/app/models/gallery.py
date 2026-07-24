from pydantic import BaseModel, Field, PositiveInt, model_validator
from tortoise.fields import (
    BigIntField,
    CharField,
    DatetimeField,
    ForeignKeyField,
    ForeignKeyRelation,
    IntField,
    ReverseRelation,
)

from app.models.base import Pageable, TortoiseModel
from app.utils.disk import is_directory


class Gallery(TortoiseModel):
    dir = CharField(max_length=4096, unique=True)
    name = CharField(max_length=64, unique=True)
    priority = IntField(unique=True)
    items: ReverseRelation["GalleryItem"]

    class Meta:
        table = "gallery"
        ordering = ["priority"]

    class PydanticMeta:
        exclude = ("items",)


class GalleryItem(TortoiseModel):
    gallery_id: int
    gallery: ForeignKeyRelation[Gallery] = ForeignKeyField(
        "models.Gallery", related_name="items", db_index=True
    )
    dir = CharField(max_length=4096)
    path = CharField(max_length=4096)
    name = CharField(max_length=255)
    size = BigIntField()
    modified_at = DatetimeField()

    class Meta:
        table = "gallery_item"
        ordering = ["-modified_at", "name"]
        unique_together = (("gallery", "path"),)

    class PydanticMeta:
        exclude = ("gallery",)


class GalleryUpsert(BaseModel):
    id: PositiveInt | None = None
    dir: str | None = Field(min_length=1, max_length=4096, default=None)
    name: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def check_dir(self):
        if not self.id and (not self.dir or not is_directory(self.dir)):
            raise ValueError(f"invalid directory: {self.dir}")
        return self


class GalleryQuery(Pageable):
    gallery_id: PositiveInt | None = None
    keyword: str | None = None
