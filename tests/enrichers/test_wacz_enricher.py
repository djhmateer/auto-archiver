import os
from zipfile import ZipFile

import pytest

from auto_archiver.core import Metadata, Media
from auto_archiver.core.consts import SetupError


@pytest.fixture
def wacz_enricher(setup_module, mock_binary_dependencies):
    configs: dict = {
        "profile": None,
        "docker_commands": None,
        "timeout": 120,
        "extract_media": False,
        "extract_screenshot": True,
        "socks_proxy_host": None,
        "socks_proxy_port": None,
        "proxy_server": None,
    }
    wacz = setup_module("wacz_extractor_enricher", configs)
    return wacz


def test_raises_error_without_docker_installed(setup_module, mocker, caplog):
    # pretend that docker isn't installed
    mocker.patch("shutil.which").return_value = None
    with pytest.raises(SetupError):
        setup_module("wacz_extractor_enricher", {})

    assert "requires external dependency 'docker' which is not available/setup" in caplog.text


def test_setup_without_docker(wacz_enricher, mocker):
    mocker.patch.dict(os.environ, {"RUNNING_IN_DOCKER": "1"}, clear=True)
    wacz_enricher.setup()
    assert not wacz_enricher.docker_in_docker


def test_setup_with_docker(wacz_enricher, mocker):
    mocker.patch.dict(os.environ, {"WACZ_ENABLE_DOCKER": "1"}, clear=True)
    wacz_enricher.setup()
    assert wacz_enricher.use_docker


def test_already_ran(wacz_enricher, metadata, mocker):
    metadata.add_media(Media("test.wacz"), id="browsertrix")
    mock_log = mocker.patch("auto_archiver.utils.custom_logger.logger.info")
    assert wacz_enricher.enrich(metadata) is True
    assert "WACZ enricher had already been executed" in mock_log.call_args[0][0]


def test_basic_call_execution(wacz_enricher, mocker):
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = mocker.Mock(returncode=0)
    metadata = Metadata().set_url("https://example.com")
    wacz_enricher.enrich(metadata)
    assert mock_run.called
    # Checks that the url is passed to the cmd
    assert "--url https://example.com" in " ".join(mock_run.call_args[0][0])


def test_window_size_passed_as_geometry(wacz_enricher, mocker):
    """browsertrix sizes its window (and so a Facebook fullPage screenshot) from the GEOMETRY env var"""
    mock_run = mocker.patch("subprocess.run")
    wacz_enricher.use_docker = True
    wacz_enricher.window_size = "1280x2048"
    wacz_enricher.enrich(Metadata().set_url("https://example.com"))
    cmd = mock_run.call_args[0][0]
    assert cmd[cmd.index("-e") + 1] == "GEOMETRY=1280x2048x16"
    assert cmd.index("-e") < cmd.index("webrecorder/browsertrix-crawler")
    assert mock_run.call_args[1]["env"]["GEOMETRY"] == "1280x2048x16"


def test_download_success(wacz_enricher, mocker) -> None:
    """Test download returns metadata on successful enrichment."""
    basic_metadata = Metadata().set_url("https://example.com")
    mocker.patch.object(wacz_enricher, "enrich", return_value=True)
    result = wacz_enricher.download(basic_metadata)
    assert result is not None
    assert isinstance(result, Metadata)
    assert result.status == "wacz: success"


def test_download_failure_skips_later_enrich(wacz_enricher, mocker, tmp_path) -> None:
    import subprocess

    wacz_enricher.tmp_dir = str(tmp_path)
    mock_run = mocker.patch("subprocess.run", side_effect=subprocess.CalledProcessError(9, ["docker"], output="", stderr=""))
    metadata = Metadata().set_url("https://example.com")
    assert wacz_enricher.download(metadata) is None
    assert wacz_enricher.enrich(metadata) is False
    assert mock_run.call_count == 1


def test_enrich_already_executed(wacz_enricher, mocker) -> None:
    """Test enrich  if already executed."""
    mock_log = mocker.patch("auto_archiver.utils.custom_logger.logger.info")
    metadata = Metadata().set_url("https://example.com")
    media = Media(filename="some_file.wacz")
    metadata.add_media(media, id="browsertrix")
    result = wacz_enricher.enrich(metadata)
    assert result is True
    assert "WACZ enricher had already been executed:" in mock_log.call_args[0][0]


def test_enrich_subprocess_exception(wacz_enricher, mocker, tmp_path) -> None:
    """Test enrich returns False when subprocess fails."""
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = False
    wacz_enricher.extract_screenshot = True
    mocker.patch("auto_archiver.utils.misc.random_str", return_value="TESTCOL")
    mocker.patch("subprocess.run", side_effect=Exception("fail"))
    basic_metadata = Metadata().set_url("https://example.com")
    result = wacz_enricher.enrich(basic_metadata)
    assert result is False


def test_extract_media(wacz_enricher, metadata, tmp_path, mocker) -> None:
    """Test extract_media_from_wacz extracts screenshot media."""
    wacz_enricher.tmp_dir = str(tmp_path)

    # Create a *real* zip file so ZipFile won't fail.
    wacz_file = tmp_path / "dummy.wacz"
    with ZipFile(wacz_file, "w") as zf:
        zf.writestr("dummy.txt", "test content")

    mocker.patch("os.listdir", return_value=[])
    warc_data = (
        b"WARC/1.0\r\n"
        b"WARC-Type: resource\r\n"
        b"Content-Type: image/png\r\n"
        b"WARC-Target-URI: http://example.com/image.png\r\n"
        b"Content-Length: 12\r\n"
        b"\r\n"
        b"image-bytes"
        b"\r\n\r\nWARC/1.0\r\n\r\n"
    )
    mock_file = mocker.mock_open(read_data=warc_data)
    mocker.patch("builtins.open", mock_file)
    metadata.add_media(Media("something.wacz"), "browsertrix")
    wacz_enricher.extract_media_from_wacz(metadata, str(wacz_file))
    assert len(metadata.media) == 2
    assert metadata.media[1].properties.get("id") == "browsertrix-screenshot-0"


def test_enrich_logs_crawler_errors_on_failure(wacz_enricher, mocker, tmp_path) -> None:
    import subprocess

    wacz_enricher.tmp_dir = str(tmp_path)
    stdout = "\n".join(
        [
            '{"timestamp":"t","logLevel":"info","context":"general","message":"Seeds","details":{}}',
            '{"timestamp":"t","logLevel":"error","context":"general","message":"Page Load Timeout","details":{}}',
            '{"timestamp":"t","logLevel":"info","context":"general","message":"Exiting","details":{}}',
        ]
    )
    mocker.patch("subprocess.run", side_effect=subprocess.CalledProcessError(9, ["docker"], output=stdout, stderr=""))
    mock_log = mocker.patch("auto_archiver.utils.custom_logger.logger.error")

    assert wacz_enricher.enrich(Metadata().set_url("https://example.com")) is False

    crawler_log = mock_log.call_args_list[-1][0][0]
    assert "Page Load Timeout" in crawler_log
    assert "Seeds" not in crawler_log


def test_summarise_crawler_output_falls_back_to_tail(wacz_enricher) -> None:
    stdout = "\n".join(f"line {i}" for i in range(50))
    summary = wacz_enricher._summarise_crawler_output(stdout, "", max_lines=5)
    assert summary.splitlines() == [f"line {i}" for i in range(45, 50)]


def test_crawler_failure_reason(wacz_enricher) -> None:
    rate_limited = '{"logLevel":"warn","message":"Page possibly rate limited, retrying"}'
    stdout = "\n".join(
        [
            '{"logLevel":"info","message":"Seeds"}',
            '{"logLevel":"warn","context":"redis","message":"ioredis error"}',
            '{"logLevel":"warn","context":"redis","message":"Waiting for redis at redis://localhost:6379/0"}',
            *[rate_limited] * 5,
            '{"logLevel":"warn","message":"Page date missing, setting to now"}',
            '{"logLevel":"error","message":"Crawl failed, no pages crawled successfully"}',
            "not json",
        ]
    )
    assert wacz_enricher._crawler_failure_reason(stdout, "") == (
        "Page possibly rate limited, retrying; Crawl failed, no pages crawled successfully"
    )
    assert wacz_enricher._crawler_failure_reason(None, None) == ""


def test_enrich_failure_first_log_line_has_reason(wacz_enricher, mocker, tmp_path) -> None:
    import subprocess

    wacz_enricher.tmp_dir = str(tmp_path)
    stdout = '{"timestamp":"t","logLevel":"error","context":"general","message":"Crawl failed, no pages crawled successfully","details":{}}'
    mocker.patch("subprocess.run", side_effect=subprocess.CalledProcessError(9, ["docker"], output=stdout, stderr=""))
    mock_log = mocker.patch("auto_archiver.utils.custom_logger.logger.error")

    assert wacz_enricher.enrich(Metadata().set_url("https://example.com")) is False

    first_line = mock_log.call_args_list[0][0][0]
    assert first_line == (
        "WACZ generation failed (exit status 9) for https://example.com: Crawl failed, no pages crawled successfully"
    )



@pytest.mark.parametrize(
    "body, route, carousel_set_id",
    [
        # post carousel
        (
            "b'av=1&route_urls[0]=%2Fphoto%2F%3Ffbid%3D1646726009098072%26set%3Dpcb.1646726145764725%26__tn__%3DEH-R'",
            ("1646726009098072", "pcb.1646726145764725", True),
            "1646726145764725",
        ),
        # post carousel set ending the route - the old slice made this non-numeric and skipped it, so still skipped
        (
            "b'route_urls[0]=%2Fphoto%2F%3Ffbid%3D8610356029060306%26set%3Dpcb.8610357259060183&routing_namespace=fb_comet'",
            ("8610356029060306", "pcb.8610357259060183", False),
            None,
        ),
        # author's album and profile photos - the old +13 slice truncated these to numeric set_ids
        (
            "b'route_urls[0]=%2Fphoto%2F%3Ffbid%3D1012033847292808%26set%3Da.107969117699290%26__tn__'",
            ("1012033847292808", "a.107969117699290", True),
            None,
        ),
        (
            "b'route_urls[0]=%2Fphoto%2F%3Ffbid%3D2439550876400757%26set%3Dpb.100022186927255.-2207520000&routing_namespace'",
            ("2439550876400757", "pb.100022186927255.-2207520000", False),
            None,
        ),
        # no set on the route
        ("b'route_urls[0]=%2Fphoto%2F%3Ffbid%3D2439550876400757&routing_namespace'", ("2439550876400757", None, False), None),
        # only the first photo route is used, as before - a later carousel route doesn't override an album one
        (
            "b'x=%2Fphoto%2F%3Ffbid%3D1%26set%3Da.107969117699290%26y=%2Fphoto%2F%3Ffbid%3D2%26set%3Dpcb.3%26'",
            ("1", "a.107969117699290", True),
            None,
        ),
        ("b'route_urls[0]=%2Fstory.php%3Fstory_fbid%3D1'", None, None),
    ],
)
def test_fb_photo_route(body, route, carousel_set_id) -> None:
    from auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher import WaczExtractorEnricher

    assert WaczExtractorEnricher._first_fb_photo_route(body) == route
    if route:
        assert WaczExtractorEnricher._fb_carousel_set_id(route[1], route[2]) == carousel_set_id


def _write_fb_photo_wacz(wacz_path, photo_url: str, route: bool = True) -> None:
    """A wacz like a crawl of a Facebook /photo page: a bulk-route-definitions request for a photo in the author's
    album (an a. set, so no carousel set_id), the full resolution image response and the browsertrix screenshot.
    Without the route it is like a video page, which has no photo routes at all"""
    from io import BytesIO

    from warcio.statusandheaders import StatusAndHeaders
    from warcio.warcwriter import WARCWriter

    warc = BytesIO()
    writer = WARCWriter(warc, gzip=True)
    route_body = b"route_urls[0]=%2Fphoto%2F%3Ffbid%3D1329142910787472%26set%3Da.132433247125117&routing_namespace=fb_comet"
    if route:
        writer.write_record(
            writer.create_warc_record(
                "https://www.facebook.com/ajax/bulk-route-definitions/",
                "request",
                payload=BytesIO(route_body),
                http_headers=StatusAndHeaders("POST /ajax/bulk-route-definitions/ HTTP/1.1", [], is_http_request=True),
            )
        )
    writer.write_record(
        writer.create_warc_record(
            photo_url,
            "response",
            payload=BytesIO(b"\xff\xd8" + b"0" * 20000),
            http_headers=StatusAndHeaders("200 OK", [("Content-Type", "image/jpeg")], protocol="HTTP/1.1"),
        )
    )
    writer.write_record(
        writer.create_warc_record(
            "urn:view:https://www.facebook.com/photo/",
            "resource",
            payload=BytesIO(b"png-bytes"),
            warc_content_type="image/png",
        )
    )
    with ZipFile(wacz_path, "w") as zf:
        zf.writestr("archive/data.warc.gz", warc.getvalue())


def test_facebook_single_photo_without_carousel_set_id(wacz_enricher, tmp_path, mocker) -> None:
    """A /photo page in the author's album has no pcb. set_id, but its image and screenshot are still extracted"""
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    mocker.patch("os.path.expanduser", return_value=str(tmp_path / "aatmp"))
    crawl = mocker.patch.object(wacz_enricher, "save_images_to_enrich_object_from_url_using_browsertrix")
    image_url = "https://scontent.xx.fbcdn.net/v/t39.30808-6/1329142910787472_n.jpg"
    wacz_file = tmp_path / "fb.wacz"
    _write_fb_photo_wacz(wacz_file, image_url)

    metadata = Metadata().set_url("https://www.facebook.com/photo/?fbid=1329142910787472&set=a.132433247125117")
    wacz_enricher.facebook_extract_media_from_wacz(metadata, str(wacz_file), metadata.get_url())

    assert [m.properties.get("id") for m in metadata.media] == ["browsertrix-screenshot-0", "warc-file-0.jpg"]
    assert metadata.media[1].get("src") == image_url
    crawl.assert_not_called()


def test_facebook_screenshot_without_carousel_set_id(wacz_enricher, tmp_path, mocker) -> None:
    """A video page has no pcb. set_id, but the browsertrix screenshot is still extracted - and only once"""
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    mocker.patch("os.path.expanduser", return_value=str(tmp_path / "aatmp"))
    crawl = mocker.patch.object(wacz_enricher, "save_images_to_enrich_object_from_url_using_browsertrix")
    wacz_file = tmp_path / "fb.wacz"
    _write_fb_photo_wacz(wacz_file, "https://scontent.xx.fbcdn.net/v/t15.5256-10/thumb.jpg", route=False)

    metadata = Metadata().set_url("https://www.facebook.com/djhmateer/videos/763070998370197")
    wacz_enricher.facebook_extract_media_from_wacz(metadata, str(wacz_file), metadata.get_url())

    assert [m.properties.get("id") for m in metadata.media] == ["browsertrix-screenshot-0"]
    with open(metadata.media[0].filename, "rb") as f:
        assert f.read() == b"png-bytes"
    crawl.assert_not_called()
