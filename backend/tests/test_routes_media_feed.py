from app.models.media import feed_excluded_ids


def test_feed_excluded_ids_parses_unique_positive_values():
    assert feed_excluded_ids("1,2,2,-3,bad,4") == [1, 2, 4]


def test_feed_excluded_ids_handles_empty_value():
    assert feed_excluded_ids(None) == []
    assert feed_excluded_ids("") == []


def test_feed_excluded_ids_is_bounded():
    value = ",".join(str(index) for index in range(1, 150))
    result = feed_excluded_ids(value)
    assert len(result) == 100
    assert result[-1] == 100
