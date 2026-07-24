import asyncio

import pytest
from lxml import etree

from app.core.exceptions import BadRequestException
from app.core.media.editor import edit_nfo, frame_positions, image_extension
from app.models.media import NFOType


def test_distributes_frame_positions_and_validates_count():
    assert frame_positions(100, 4) == [20.0, 40.0, 60.0, 80.0]
    assert len(frame_positions(100, 24)) == 24
    with pytest.raises(BadRequestException):
        frame_positions(100, 0)
    with pytest.raises(BadRequestException):
        frame_positions(100, 25)


def test_detects_supported_image_signatures():
    assert image_extension(b"\xff\xd8\xffdata") == ".jpg"
    assert image_extension(b"\x89PNG\r\n\x1a\ndata") == ".png"
    assert image_extension(b"GIF89adata") == ".gif"
    assert image_extension(b"RIFF1234WEBPdata") == ".webp"
    assert image_extension(b"<svg></svg>") is None


def test_edits_nfo_without_replacing_other_metadata(tmp_path):
    path = tmp_path / "movie.nfo"
    path.write_text(
        "<movie><title>Original</title><actor><name>Actor</name></actor>"
        "<plot>Old</plot><art><fanart>backdrop.jpg</fanart></art></movie>",
        encoding="utf-8",
    )

    asyncio.run(
        edit_nfo(
            NFOType.MOVIE,
            str(path),
            title="Ignored",
            plot="New description",
            poster="media/custom.jpg",
        )
    )

    root = etree.parse(path).getroot()
    assert root.findtext("title") == "Original"
    assert root.findtext("actor/name") == "Actor"
    assert root.findtext("plot") == "New description"
    assert root.findtext("art/poster") == "media/custom.jpg"
    assert root.findtext("art/fanart") == "backdrop.jpg"


def test_creates_and_clears_editable_nfo_fields(tmp_path):
    path = tmp_path / "show.nfo"
    asyncio.run(
        edit_nfo(
            NFOType.TV_SHOW,
            str(path),
            title="Series",
            plot="Description",
            poster="poster.jpg",
        )
    )
    asyncio.run(
        edit_nfo(
            NFOType.TV_SHOW,
            str(path),
            title="Series",
            plot=None,
            poster=None,
        )
    )

    root = etree.parse(path).getroot()
    assert root.tag == "tv_show" or root.tag == "tvshow"
    assert root.findtext("title") == "Series"
    assert root.find("plot") is None
    assert root.find("art") is None


def test_edits_episode_nfo_without_replacing_episode_numbers(tmp_path):
    path = tmp_path / "show.s01e02.nfo"
    path.write_text(
        "<episode><title>Episode 2</title><season>1</season><episode>2</episode>"
        "<plot>Old</plot></episode>",
        encoding="utf-8",
    )

    asyncio.run(
        edit_nfo(
            NFOType.EPISODE,
            str(path),
            title="Ignored",
            plot="Updated episode description",
            poster="media/episode.jpg",
        )
    )

    root = etree.parse(path).getroot()
    assert root.tag == "episode"
    assert root.findtext("title") == "Episode 2"
    assert root.findtext("season") == "1"
    assert root.findtext("episode") == "2"
    assert root.findtext("plot") == "Updated episode description"
