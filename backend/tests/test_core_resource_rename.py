import asyncio
from pathlib import Path

import pytest

from app.core.exceptions import ErrorCode, KaloscopeException
from app.core.resource_rename import (
    normalize_resource_name,
    rename_destination,
    rename_paths,
    replace_path_prefix,
    sidecar_destination,
)


def test_validates_resource_names_and_preserves_extension():
    source = Path("/library/Movie.mkv")
    assert (
        rename_destination(source, "New name", directory=False).name == "New name.mkv"
    )
    assert (
        rename_destination(source, "New name.mkv", directory=False).name
        == "New name.mkv"
    )
    assert normalize_resource_name("Series") == "Series"

    for value in (
        "",
        ".",
        "..",
        "bad/name",
        "bad:name",
        "trailing.",
        "trailing ",
        "bad\tname",
    ):
        with pytest.raises(KaloscopeException) as error:
            normalize_resource_name(value)
        assert error.value.message == ErrorCode.BAD_REQUEST


def test_maps_nested_paths_and_sidecars():
    source = Path("/library/Old")
    destination = Path("/library/New")
    assert replace_path_prefix("/library/Old/Season 1/a.mkv", source, destination) == (
        "/library/New/Season 1/a.mkv"
    )
    assert replace_path_prefix("/other/a.mkv", source, destination) == "/other/a.mkv"

    video = source / "Episode 1.mkv"
    renamed = source / "Pilot.mkv"
    assert (
        sidecar_destination(source / "Episode 1.nfo", video, renamed).name
        == "Pilot.nfo"
    )
    assert (
        sidecar_destination(source / "Episode 1.zh-CN.ass", video, renamed).name
        == "Pilot.zh-CN.ass"
    )


def test_renames_multiple_paths_and_rejects_collisions(tmp_path):
    video = tmp_path / "old.mkv"
    nfo = tmp_path / "old.nfo"
    video.write_bytes(b"video")
    nfo.write_text("metadata", encoding="utf-8")

    asyncio.run(
        rename_paths([(video, tmp_path / "new.mkv"), (nfo, tmp_path / "new.nfo")])
    )
    assert (tmp_path / "new.mkv").read_bytes() == b"video"
    assert (tmp_path / "new.nfo").read_text(encoding="utf-8") == "metadata"

    collision = tmp_path / "collision.mkv"
    collision.write_bytes(b"existing")
    with pytest.raises(KaloscopeException) as error:
        asyncio.run(rename_paths([(tmp_path / "new.mkv", collision)]))
    assert error.value.message == ErrorCode.NAME_ALREADY_EXISTS
