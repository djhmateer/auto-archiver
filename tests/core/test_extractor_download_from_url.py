"""
Extractor.download_from_url retries an HTTP 429 (eg from cdninstagram) instead of failing straight away.
Uses instagram_api_extractor only as a concrete Extractor - the code under test is in core/extractor.py.
"""

from unittest.mock import MagicMock

import pytest
import requests


def _response(status_code: int, headers: dict = None, body: bytes = b"data"):
    r = MagicMock()
    r.status_code = status_code
    r.headers = {"Content-Type": "video/mp4", **(headers or {})}
    r.iter_content.return_value = [body]
    if status_code >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(f"{status_code} Client Error")
    return r


@pytest.fixture
def extractor(setup_module):
    return setup_module(
        "instagram_api_extractor", {"access_token": "t", "api_endpoint": "https://api.example.com", "full_profile": False}
    )


@pytest.fixture
def mock_sleep(mocker):
    return mocker.patch("auto_archiver.core.extractor.time.sleep")


def test_429_then_success_retries_and_downloads(extractor, mocker, mock_sleep):
    mock_get = mocker.patch(
        "auto_archiver.core.extractor.requests.get", side_effect=[_response(429), _response(200)]
    )

    filename = extractor.download_from_url("https://cdn.example.com/video.mp4", verbose=False)

    assert filename is not None
    with open(filename, "rb") as f:
        assert f.read() == b"data"
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once_with(10)


def test_429_honours_retry_after_capped(extractor, mocker, mock_sleep):
    mocker.patch(
        "auto_archiver.core.extractor.requests.get",
        side_effect=[_response(429, {"Retry-After": "5"}), _response(429, {"Retry-After": "600"}), _response(200)],
    )

    assert extractor.download_from_url("https://cdn.example.com/video.mp4", verbose=False)
    assert [c.args[0] for c in mock_sleep.call_args_list] == [5, extractor.MAX_429_WAIT_SECONDS]


def test_429_every_attempt_gives_up_and_returns_none(extractor, mocker, mock_sleep):
    mock_get = mocker.patch("auto_archiver.core.extractor.requests.get", side_effect=lambda *a, **k: _response(429))

    assert extractor.download_from_url("https://cdn.example.com/video.mp4", verbose=False) is None
    assert mock_get.call_count == extractor.MAX_429_ATTEMPTS
    assert mock_sleep.call_count == extractor.MAX_429_ATTEMPTS - 1


def test_other_errors_are_not_retried(extractor, mocker, mock_sleep):
    mock_get = mocker.patch("auto_archiver.core.extractor.requests.get", return_value=_response(404))

    assert extractor.download_from_url("https://cdn.example.com/video.mp4", verbose=False) is None
    assert mock_get.call_count == 1
    mock_sleep.assert_not_called()


@pytest.mark.parametrize("header,expected", [(None, 20), ("abc", 20), ("Wed, 21 Oct 2026 07:28:00 GMT", 20), ("0", 1)])
def test_retry_after_seconds_fallbacks(extractor, header, expected):
    assert extractor._retry_after_seconds(header, default=20) == expected
