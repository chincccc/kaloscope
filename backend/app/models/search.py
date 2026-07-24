from enum import StrEnum

from pydantic import Field

from app.models.base import Pageable


class UnifiedSearchType(StrEnum):
    MOVIE = "movie"
    TV_SHOW = "tv_show"
    GALLERY_BOOK = "gallery_book"
    EPISODE = "episode"
    IMAGE = "image"


class UnifiedSearchQuery(Pageable):
    keyword: str = Field(min_length=1, max_length=255)
    types: str | None = Field(max_length=128, default=None)
    rating_filters: str | None = Field(max_length=1024, default=None)
    rating_dimension: str | None = Field(max_length=32, default=None)
    rating_min: int | None = Field(ge=1, le=10, default=None)

    @property
    def selected_types(self) -> set[UnifiedSearchType]:
        if not self.types:
            return set(UnifiedSearchType)
        try:
            values = {UnifiedSearchType(value) for value in self.types.split(",")}
        except ValueError:
            return set()
        return values

    @property
    def selected_rating_filters(self) -> list[tuple[str, int]]:
        filters: dict[str, int] = {}
        if self.rating_filters:
            for value in self.rating_filters.split(","):
                dimension, separator, minimum = value.partition(":")
                if not separator or not dimension or len(dimension) > 32:
                    continue
                try:
                    score = int(minimum)
                except ValueError:
                    continue
                if 1 <= score <= 10:
                    filters[dimension] = score
        elif self.rating_dimension and self.rating_min is not None:
            filters[self.rating_dimension] = self.rating_min
        return list(filters.items())
