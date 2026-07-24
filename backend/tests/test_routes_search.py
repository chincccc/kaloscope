from app.routes.search import _matches_rating_filters, _page


def test_search_group_pagination():
    items = [{"id": value} for value in range(7)]

    assert _page(items, 0, 2) == items
    assert _page(items, 1, 2) == items[:2]
    assert _page(items, 2, 2) == items[2:4]
    assert _page(items, 4, 2) == items[6:]


def test_search_group_pagination_beyond_results():
    assert _page([{"id": 1}], 3, 20) == []


def test_search_rating_filters_require_every_dimension():
    values = [
        {"key": "default", "score": 8},
        {"key": "funny", "score": 6},
    ]

    assert _matches_rating_filters(values, [("default", 8), ("funny", 6)])
    assert not _matches_rating_filters(values, [("default", 9), ("funny", 6)])
    assert not _matches_rating_filters(values, [("default", 8), ("visual", 1)])


def test_search_rating_filters_accept_no_conditions():
    assert _matches_rating_filters([], [])
