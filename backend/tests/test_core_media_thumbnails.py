import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.media import thumbnails


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("first", "first"),
        ("middle", "middle"),
        ("random", "random"),
        ("invalid", "first"),
        (None, "first"),
    ],
)
def test_thumbnail_source_is_validated(monkeypatch, value, expected):
    monkeypatch.setattr(
        thumbnails.GlobalConfig,
        "get_or_none",
        AsyncMock(return_value=SimpleNamespace(value=value)),
    )
    assert asyncio.run(thumbnails.thumbnail_source()) == expected


def test_thumbnail_source_defaults_to_first_frame(monkeypatch):
    monkeypatch.setattr(
        thumbnails.GlobalConfig,
        "get_or_none",
        AsyncMock(return_value=None),
    )
    assert asyncio.run(thumbnails.thumbnail_source()) == "first"


def test_thumbnail_positions(monkeypatch):
    monkeypatch.setattr(thumbnails.random, "random", lambda: 0.25)

    assert thumbnails.thumbnail_position("first", 100) == 0
    assert thumbnails.thumbnail_position("middle", 100) == 50
    assert thumbnails.thumbnail_position("random", 100) == 25
    assert thumbnails.thumbnail_position("middle", None) == 0


def test_nfo_poster_reference_detection(tmp_path):
    nfo = tmp_path / "movie.nfo"
    nfo.write_text(
        "<movie><art><poster>media/custom.jpg</poster></art></movie>",
        encoding="utf-8",
    )

    assert thumbnails.nfo_references_poster(str(nfo), "media/custom.jpg")
    assert not thumbnails.nfo_references_poster(str(nfo), "media/automatic.jpg")
    assert not thumbnails.nfo_references_poster(
        str(tmp_path / "missing.nfo"), "media/custom.jpg"
    )
