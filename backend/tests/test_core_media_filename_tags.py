import pytest

from app.core.exceptions import KaloscopeException
from app.core.media.filename_tags import filename_tags, tagged_resource_name


def test_extracts_tags_separated_by_spaces_and_underscores():
    name = "1608 - #Quan冉有点饿 #拖拉大王  __#花火 #崩铁.mp4"

    assert filename_tags(name) == ["Quan冉有点饿", "拖拉大王", "花火", "崩铁"]


def test_replaces_tags_and_preserves_suffix_case():
    assert tagged_resource_name("Jh-283.MP4", ["搞笑"], directory=False) == (
        "Jh-283 #搞笑.MP4"
    )
    assert (
        tagged_resource_name(
            "1608 - #Quan冉有点饿 __#花火.mp4",
            ["拖拉大王", "崩铁"],
            directory=False,
        )
        == "1608 - #拖拉大王 #崩铁.mp4"
    )

    assert (
        tagged_resource_name("7 - #原神 #cos #八重神子..mp4", ["搞笑"], directory=False)
        == "7 - #搞笑.mp4"
    )


def test_allows_a_directory_name_made_only_of_tags():
    assert (
        tagged_resource_name("#cos #福利姬", ["原神", "搞笑"], directory=True)
        == "#原神 #搞笑"
    )


def test_replaces_directory_tags_without_treating_dots_as_suffixes():
    assert tagged_resource_name("Show.2026 #旧", ["新"], directory=True) == (
        "Show.2026 #新"
    )


def test_rejects_ambiguous_tag_separators():
    with pytest.raises(KaloscopeException):
        tagged_resource_name("Movie.mp4", ["两个 标签"], directory=False)
