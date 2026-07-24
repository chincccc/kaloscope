import asyncio
from types import SimpleNamespace

from app.core.media import thumbnails


class UpdateQuery:
    def __init__(self, updates):
        self.updates = updates

    async def update(self, **values):
        self.updates.append(values)
        return 1


def classify(monkeypatch, item):
    updates = []
    monkeypatch.setattr(
        thumbnails.MediaItem,
        "filter",
        lambda **_: UpdateQuery(updates),
    )
    source = asyncio.run(thumbnails.classify_unmarked_poster(item))
    return source, updates


def test_classifies_legacy_generated_poster_as_auto(monkeypatch):
    item = SimpleNamespace(
        id=1,
        poster="media/generated.jpg",
        poster_source=None,
        nfo_path=None,
    )

    source, updates = classify(monkeypatch, item)

    assert source == thumbnails.POSTER_SOURCE_AUTO
    assert item.poster_source == thumbnails.POSTER_SOURCE_AUTO
    assert updates == [{"poster_source": thumbnails.POSTER_SOURCE_AUTO}]


def test_classifies_external_poster_as_custom(monkeypatch):
    item = SimpleNamespace(
        id=1,
        poster="https://example.com/poster.jpg",
        poster_source=None,
        nfo_path=None,
    )

    source, updates = classify(monkeypatch, item)

    assert source == thumbnails.POSTER_SOURCE_CUSTOM
    assert item.poster_source == thumbnails.POSTER_SOURCE_CUSTOM
    assert updates == [{"poster_source": thumbnails.POSTER_SOURCE_CUSTOM}]


def test_classifies_nfo_referenced_managed_poster_as_custom(monkeypatch, tmp_path):
    nfo = tmp_path / "movie.nfo"
    nfo.write_text(
        "<movie><art><poster>media/custom.jpg</poster></art></movie>",
        encoding="utf-8",
    )
    item = SimpleNamespace(
        id=1,
        poster="media/custom.jpg",
        poster_source=None,
        nfo_path=str(nfo),
    )

    source, updates = classify(monkeypatch, item)

    assert source == thumbnails.POSTER_SOURCE_CUSTOM
    assert item.poster_source == thumbnails.POSTER_SOURCE_CUSTOM
    assert updates == [{"poster_source": thumbnails.POSTER_SOURCE_CUSTOM}]
