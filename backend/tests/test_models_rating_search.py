import pytest

from app.models.search import UnifiedSearchQuery, UnifiedSearchType
from app.services.rating import DEFAULT_DIMENSION, custom_dimensions


def test_unified_search_defaults_to_all_types():
    query = UnifiedSearchQuery(keyword="test", page_num=0)

    assert query.selected_types == set(UnifiedSearchType)


def test_unified_search_parses_selected_types():
    query = UnifiedSearchQuery(
        keyword="test", types="movie,gallery_book,image", page_num=0
    )

    assert query.selected_types == {
        UnifiedSearchType.MOVIE,
        UnifiedSearchType.GALLERY_BOOK,
        UnifiedSearchType.IMAGE,
    }


def test_unified_search_rejects_unknown_type_as_empty_selection():
    query = UnifiedSearchQuery(keyword="test", types="movie,unknown", page_num=0)

    assert query.selected_types == set()


def test_unified_search_parses_multiple_rating_filters():
    query = UnifiedSearchQuery(
        keyword="test",
        rating_filters="default:7,funny:5,invalid,visual:11",
        page_num=0,
    )

    assert query.selected_rating_filters == [("default", 7), ("funny", 5)]


def test_unified_search_rating_filters_deduplicate_dimensions():
    query = UnifiedSearchQuery(
        keyword="test", rating_filters="default:3,default:8", page_num=0
    )

    assert query.selected_rating_filters == [("default", 8)]


def test_unified_search_supports_legacy_rating_filter():
    query = UnifiedSearchQuery(
        keyword="test", rating_dimension="default", rating_min=6, page_num=0
    )

    assert query.selected_rating_filters == [("default", 6)]


def test_custom_rating_dimensions_are_sanitized():
    preferences = {
        "rating_dimensions": [
            {"key": "a" * 32, "name": " 搞笑度 "},
            {"key": "a" * 32, "name": "duplicate"},
            {"key": "short", "name": "invalid"},
            {"key": "b" * 32, "name": ""},
            "invalid",
        ]
    }

    assert custom_dimensions(preferences) == [
        {
            "key": "a" * 32,
            "name": "搞笑度",
            "removable": True,
        }
    ]


@pytest.mark.parametrize("preferences", [None, {}, {"rating_dimensions": None}])
def test_custom_rating_dimensions_accept_missing_values(preferences):
    assert custom_dimensions(preferences) == []


def test_default_rating_dimension_is_builtin():
    assert DEFAULT_DIMENSION == {
        "key": "default",
        "name": "评分",
        "removable": False,
    }
