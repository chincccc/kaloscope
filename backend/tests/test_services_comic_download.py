from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from app.core.exceptions import KaloscopeException
from app.models.download import BuiltinDownloadType, ComicDownloadAdd
from app.services.comic_download import (
    comic_archive_filename,
    download_resource_stem,
    enrich_comic_archive,
    validate_comic_archive,
    video_filename,
)


def comic_add(**overrides) -> ComicDownloadAdd:
    values = {
        "url": "https://example.com/download/123",
        "title": "Comic Title",
        "gallery_id": 1,
    }
    values.update(overrides)
    return ComicDownloadAdd(**values)


def test_comic_archive_filename_uses_url_suffix():
    add = comic_add(url="https://example.com/files/archive.zip?token=secret")

    assert comic_archive_filename(add) == "Comic Title.zip"


def test_comic_archive_filename_defaults_to_cbz_and_preserves_explicit_name():
    add = comic_add(filename="Custom Name")

    assert comic_archive_filename(add) == "Custom Name.cbz"


def test_comic_download_rejects_non_http_url_and_header_injection():
    with pytest.raises(ValidationError):
        comic_add(url="file:///etc/passwd")
    with pytest.raises(ValidationError):
        comic_add(headers={"Referer": "https://example.com\r\nInjected: true"})


def test_video_download_requires_media_library_and_preserves_filename():
    with pytest.raises(ValidationError):
        ComicDownloadAdd(
            type="video",
            url="https://example.com/video.mp4",
            title="Video Title",
        )

    add = ComicDownloadAdd(
        type="video",
        url="https://example.com/video.mp4?token=secret",
        title="Video Title",
        media_lib_id=1,
    )
    assert add.type == BuiltinDownloadType.VIDEO
    assert video_filename(add) == "Video Title.mp4"


def test_hls_download_always_uses_mp4_filename():
    add = ComicDownloadAdd(
        type="hls",
        url="https://example.com/master.m3u8",
        title="Stream Title",
        filename="Custom Name.mkv",
        media_lib_id=1,
    )
    assert video_filename(add) == "Custom Name.mp4"


def test_download_filename_sanitizes_external_titles_and_html_entities():
    assert (
        download_resource_stem("Title: Part / One &amp; Two?.mp4", ".mp4")
        == "Title Part One & Two"
    )


def test_download_filename_limits_utf8_byte_length():
    name = download_resource_stem("影" * 200)

    assert len(name.encode("utf-8")) <= 220


def test_validate_comic_archive_accepts_zip_with_image(tmp_path):
    archive = tmp_path / "comic.cbz"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("Chapter 1/001.jpg", b"image")

    validate_comic_archive(archive)


def test_validate_comic_archive_rejects_non_comic_zip(tmp_path):
    archive = tmp_path / "empty.zip"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("readme.txt", b"nothing")

    with pytest.raises(KaloscopeException):
        validate_comic_archive(archive)


def test_enrich_comic_archive_preserves_cover(tmp_path):
    archive = tmp_path / "comic.cbz"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("Chapter 1/001.jpg", b"page")

    enrich_comic_archive(archive, (b"cover", ".webp"))

    with ZipFile(archive) as zip_file:
        assert zip_file.read("000000_cover.webp") == b"cover"


def test_enrich_comic_archive_keeps_existing_cover(tmp_path):
    archive = tmp_path / "comic.cbz"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("folder.jpg", b"original cover")
        zip_file.writestr("001.jpg", b"page")

    enrich_comic_archive(archive, (b"new cover", ".jpg"))

    with ZipFile(archive) as zip_file:
        assert zip_file.read("folder.jpg") == b"original cover"
        assert "000000_cover.jpg" not in zip_file.namelist()
