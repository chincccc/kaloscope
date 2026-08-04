"""Unit tests for gallery file discovery and reader ordering."""

import asyncio
from unittest.mock import AsyncMock, Mock
from zipfile import ZipFile

from app.core.config import KaloscopeConfig
from app.core.gallery_archive import (
    decode_archive_member,
    encode_archive_member,
    resolve_gallery_image,
)
from app.core.gallery_thumbnail import gallery_thumbnail_fingerprint
from app.models.gallery import Gallery, GalleryItem
from app.services.gallery import (
    GalleryService,
    build_book_index,
    chapter_directory_sort_key,
    discover_images,
    gallery_book_key,
    natural_sort_key,
    replace_gallery_item_path,
)


def test_discovers_supported_images_recursively(tmp_path):
    nested = tmp_path / "album"
    nested.mkdir()
    photo = nested / "Photo.JPG"
    photo.write_bytes(b"image")
    (nested / "notes.txt").write_text("ignore", encoding="utf-8")

    images = discover_images(str(tmp_path))

    assert list(images) == [str(photo.resolve())]
    directory, name, size, modified_at = images[str(photo.resolve())]
    assert directory == str(nested.resolve())
    assert name == "Photo.JPG"
    assert size == 5
    assert modified_at.tzinfo is not None


def test_ignores_unsupported_and_directory_entries(tmp_path):
    (tmp_path / "fake.png").mkdir()
    (tmp_path / "vector.svg").write_text("<svg />", encoding="utf-8")

    assert discover_images(str(tmp_path)) == {}


def test_discovers_zip_as_a_book_and_preserves_chapters(tmp_path):
    archive = tmp_path / "Comic 12.zip"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("Comic 12/Chapter 2/10.jpg", b"ten")
        zip_file.writestr("Comic 12/Chapter 2/2.jpg", b"two")
        zip_file.writestr("Comic 12/readme.txt", b"ignore")

    images = discover_images(str(tmp_path))

    assert len(images) == 2
    rows = sorted(images.items(), key=lambda row: row[1][1])
    assert [row[1][1] for row in rows] == ["10.jpg", "2.jpg"]
    assert {row[1][0] for row in rows} == {str(tmp_path / "Comic 12" / "Chapter 2")}
    assert {decode_archive_member(row[0])[0] for row in rows} == {archive.resolve()}
    assert (
        build_book_index(
            str(tmp_path),
            [
                {"id": index, "dir": value[0], "name": value[1]}
                for index, value in enumerate(images.values(), 1)
            ],
        )[0]["name"]
        == "Comic 12"
    )


def test_discovers_cbz_pages_at_archive_root_as_unfiled_chapter(tmp_path):
    archive = tmp_path / "Album.cbz"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("001.webp", b"first")

    images = discover_images(str(tmp_path))

    assert len(images) == 1
    _, (directory, name, size, _) = next(iter(images.items()))
    assert directory == str(tmp_path / "Album")
    assert name == "001.webp"
    assert size == 5


def test_discovers_windows_archive_paths_as_naturally_sorted_chapters(tmp_path):
    archive = tmp_path / "Windows Paths.zip"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("Comic\\Chapter 10\\001.jpg", b"ten")
        zip_file.writestr("Comic\\Chapter 2\\001.jpg", b"two")

    images = discover_images(str(tmp_path))
    directories = {value[0] for value in images.values()}
    book_root = str(tmp_path / "Windows Paths")

    assert sorted(
        directories,
        key=lambda directory: chapter_directory_sort_key(book_root, directory),
    ) == [
        str(tmp_path / "Windows Paths" / "Chapter 2"),
        str(tmp_path / "Windows Paths" / "Chapter 10"),
    ]


def test_chapter_sort_ignores_inconsistent_number_separators(tmp_path):
    book_root = str(tmp_path / "Comic")
    directories = [
        str(tmp_path / "Comic" / "Series108"),
        str(tmp_path / "Comic" / "Series 2"),
        str(tmp_path / "Comic" / "Series8"),
        str(tmp_path / "Comic" / "Series - \u7b2c117\u8bdd"),
    ]

    assert sorted(
        directories,
        key=lambda directory: chapter_directory_sort_key(book_root, directory),
    ) == [
        str(tmp_path / "Comic" / "Series 2"),
        str(tmp_path / "Comic" / "Series8"),
        str(tmp_path / "Comic" / "Series108"),
        str(tmp_path / "Comic" / "Series - \u7b2c117\u8bdd"),
    ]


def test_replaces_archive_path_when_archive_is_renamed(tmp_path):
    source = tmp_path / "Old.zip"
    destination = tmp_path / "New.zip"
    encoded = encode_archive_member(source, "Chapter/001.jpg")
    replaced = replace_gallery_item_path(encoded, source, destination)

    assert decode_archive_member(replaced) == (destination, "Chapter/001.jpg")


def test_resolves_archive_member_to_lazy_cache(tmp_path, monkeypatch):
    archive = tmp_path / "Comic.zip"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("001.jpg", b"image contents")
    cache = tmp_path / "cache"
    monkeypatch.setattr(
        KaloscopeConfig,
        "get_workspace",
        classmethod(lambda _, name: str(cache)),
    )

    resolved = asyncio.run(
        resolve_gallery_image(encode_archive_member(archive, "001.jpg"))
    )

    assert resolved.read_bytes() == b"image contents"
    assert cache in resolved.parents


def test_ignores_unsafe_archive_members(tmp_path):
    archive = tmp_path / "Unsafe.zip"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("../outside.jpg", b"unsafe")
        zip_file.writestr("safe.jpg", b"safe")

    images = discover_images(str(tmp_path))

    assert len(images) == 1
    assert next(iter(images.values()))[1] == "safe.jpg"


def test_natural_sort_key_orders_numbered_chapters_and_pages():
    values = ["10.jpg", "2.jpg", "001.jpg", "page11.jpg", "page3.jpg"]

    assert sorted(values, key=natural_sort_key) == [
        "001.jpg",
        "2.jpg",
        "10.jpg",
        "page3.jpg",
        "page11.jpg",
    ]


def test_natural_sort_key_handles_mixed_numeric_and_text_segments():
    values = ["Chapter A", "Chapter 10", "Chapter 2"]

    assert sorted(values, key=natural_sort_key) == [
        "Chapter 2",
        "Chapter 10",
        "Chapter A",
    ]


def test_build_book_index_groups_and_naturally_sorts_books():
    values = [
        {"id": 3, "dir": "/library/Book 10", "name": "2.jpg"},
        {"id": 2, "dir": "/library/Book 2", "name": "10.jpg"},
        {"id": 1, "dir": "/library/Book 2", "name": "2.jpg"},
        {"id": 4, "dir": "/library", "name": "root.jpg"},
    ]

    books = build_book_index("/library", values)

    assert books == [
        {
            "id": 1,
            "name": "Book 2",
            "tags": [],
            "item_count": 2,
            "uncategorized": False,
        },
        {
            "id": 3,
            "name": "Book 10",
            "tags": [],
            "item_count": 1,
            "uncategorized": False,
        },
        {
            "id": 4,
            "name": None,
            "tags": [],
            "item_count": 1,
            "uncategorized": True,
        },
    ]


def test_gallery_book_key_uses_first_folder_and_groups_root_images():
    root = "/library"

    assert gallery_book_key(root, "/library") is None
    assert gallery_book_key(root, "/library/Book A") == "Book A"
    assert gallery_book_key(root, "/library/Book A/Volume 1/Chapter 2") == "Book A"


def test_build_book_index_extracts_tags_from_book_folder():
    books = build_book_index(
        "/library",
        [
            {
                "id": 1,
                "dir": "/library/Book #搞笑 __#收藏",
                "name": "1.jpg",
            }
        ],
    )

    assert books[0]["tags"] == ["搞笑", "收藏"]


def test_book_index_uses_persisted_rows_while_scan_is_running(monkeypatch):
    query = Mock()
    query.values = AsyncMock(
        return_value=[
            {
                "id": 1,
                "dir": "/library/Large Album",
                "name": "1.jpg",
            }
        ]
    )
    monkeypatch.setattr(GalleryItem, "filter", Mock(return_value=query))
    gallery_id = 999
    GalleryService._book_cache.pop(gallery_id, None)
    GalleryService._scanning_ids.add(gallery_id)
    try:
        books = asyncio.run(GalleryService.book_index(gallery_id, "/library"))
    finally:
        GalleryService._scanning_ids.discard(gallery_id)
        GalleryService._book_cache.pop(gallery_id, None)

    assert books == [
        {
            "id": 1,
            "name": "Large Album",
            "tags": [],
            "item_count": 1,
            "uncategorized": False,
        }
    ]


def test_gallery_thumbnail_fingerprint_changes_with_source(tmp_path):
    source = tmp_path / "cover.jpg"
    source.write_bytes(b"first")
    first = gallery_thumbnail_fingerprint(source)

    source.write_bytes(b"second version")

    assert gallery_thumbnail_fingerprint(source) != first


def test_recover_item_returns_first_current_item_from_same_book(monkeypatch):
    stale = Mock(id=10, gallery_id=1, dir="/library/Book 2/old")
    replacement = Mock(id=21)
    query = Mock()
    query.values = AsyncMock(
        return_value=[
            {"id": 31, "dir": "/library/Book 10", "name": "001.jpg"},
            {"id": 22, "dir": "/library/Book 2", "name": "10.jpg"},
            {"id": 21, "dir": "/library/Book 2", "name": "2.jpg"},
        ]
    )
    monkeypatch.setattr(
        Gallery, "get", AsyncMock(return_value=Mock(id=1, dir="/library"))
    )
    monkeypatch.setattr(GalleryService, "refresh_now", AsyncMock())
    monkeypatch.setattr(GalleryItem, "filter", Mock(return_value=query))
    monkeypatch.setattr(GalleryItem, "get_or_none", AsyncMock(return_value=replacement))

    recovered = asyncio.run(GalleryService.recover_item(stale))

    assert recovered is replacement
    GalleryItem.get_or_none.assert_awaited_once_with(id=21)
