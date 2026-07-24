from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from app.core.exceptions import KaloscopeException
from app.models.download import ComicDownloadAdd
from app.services.comic_download import comic_archive_filename, validate_comic_archive


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
