from app.models.download import ComicDownloadQuery, DownloadState


def test_comic_download_query_treats_null_state_as_empty():
    query = ComicDownloadQuery(page_num=1, state="null")

    assert query.state is None


def test_comic_download_query_accepts_real_state():
    query = ComicDownloadQuery(page_num=1, state="downloading")

    assert query.state == DownloadState.DOWNLOADING
