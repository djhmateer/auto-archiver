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


FB_PHOTO_URL = "https://www.facebook.com/photo/?fbid=1329142910787472&set=a.132433247125117"
FB_PHOTO_IMAGE_URL = "https://scontent.xx.fbcdn.net/v/t1.6435-9/153394039_1329142914120805_n.jpg?stp=dst-jpg_tt6&_nc_cat=111"
FB_STICKER_URL = "https://scontent.xx.fbcdn.net/v/t39.1997-6/12057124_787824104676675_170142219_n.png?stp=dst-png_p240x240"
# shaped like a /photo page's html - the photo's node with its full res image, the url's slashes escaped
FB_PHOTO_HTML = (
    b'{"__isNode":"Photo","id":"1329142910787472","owner":{"__typename":"User","id":"100010753278573"},'
    b'"created_time":1614097595,"image":{"uri":"' + FB_PHOTO_IMAGE_URL.replace("/", "\\/").encode() + b'"}}'
)


def _write_fb_photo_wacz(wacz_path, photo_url: str, route: bool = True, page_html: bytes = None) -> None:
    """A wacz like a crawl of a Facebook /photo page: a bulk-route-definitions request for a photo in the author's
    album (an a. set, so no carousel set_id), the full resolution image response and the browsertrix screenshot.
    Without the route it is like a video page, which has no photo routes at all.
    With page_html, also the page's html and a comment sticker png big enough to pass the size filters"""
    from io import BytesIO

    from warcio.statusandheaders import StatusAndHeaders
    from warcio.warcwriter import WARCWriter

    warc = BytesIO()
    writer = WARCWriter(warc, gzip=True)
    route_body = (
        b"route_urls[0]=%2Fphoto%2F%3Ffbid%3D1329142910787472%26set%3Da.132433247125117&routing_namespace=fb_comet"
        b"&__aaid=0&__user=0&__a=1"
    )
    if route:
        writer.write_record(
            writer.create_warc_record(
                "https://www.facebook.com/ajax/bulk-route-definitions/",
                "request",
                payload=BytesIO(route_body),
                http_headers=StatusAndHeaders("POST /ajax/bulk-route-definitions/ HTTP/1.1", [], is_http_request=True),
            )
        )
    if page_html is not None:
        writer.write_record(
            writer.create_warc_record(
                FB_PHOTO_URL,
                "response",
                payload=BytesIO(page_html),
                http_headers=StatusAndHeaders("200 OK", [("Content-Type", 'text/html; charset="utf-8"')], protocol="HTTP/1.1"),
            )
        )
        writer.write_record(
            writer.create_warc_record(
                FB_STICKER_URL,
                "response",
                payload=BytesIO(b"\x89PNG" + b"0" * 60000),
                http_headers=StatusAndHeaders("200 OK", [("Content-Type", "image/png")], protocol="HTTP/1.1"),
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


@pytest.mark.parametrize(
    "page_html, srcs",
    [
        # the photo's node gives its image, so the comment sticker isn't kept
        (FB_PHOTO_HTML, [FB_PHOTO_IMAGE_URL]),
        # no photo node in the html - every image passing the size filters, as before
        (b"<html></html>", [FB_STICKER_URL, FB_PHOTO_IMAGE_URL]),
    ],
)
def test_facebook_single_photo_keeps_only_the_photos_image(wacz_enricher, tmp_path, mocker, page_html, srcs) -> None:
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    wacz_file = tmp_path / "fb.wacz"
    _write_fb_photo_wacz(wacz_file, FB_PHOTO_IMAGE_URL, page_html=page_html)

    metadata = Metadata().set_url(FB_PHOTO_URL)
    wacz_enricher.facebook_extract_media_from_wacz(metadata, str(wacz_file), FB_PHOTO_URL)

    assert [m.get("src") for m in metadata.media if m.get("src")] == srcs


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


FB_POST_URL = "https://www.facebook.com/khitthitnews/posts/pfbid0PTvT6iAccWqatvbDQNuqpFwL5WKzHuLK4QjP97Fwut637CV3XXQU53z1s2bJMAKwl"
# shaped like the post page's html - the post's own story, then a recommended group post (eg the car) with its carousel
FB_OWN_STORY = (
    b'{"post_id":"1646726145764725","attachments":[{"mediaset_token":"pcb.1646726145764725"}],'
    rb'"tracking":"{\"top_level_post_id\":\"1646726145764725\",\"page_id\":\"385165108587508\"}"}'
)
FB_RECOMMENDED_STORY = (
    b'{"post_id":"1424364323199649","attachments":[{"mediaset_token":"pcb.1424364323199649"}],'
    rb'"tracking":"{\"top_level_post_id\":\"1424364323199649\",\"originated_from_recommendation\":\"1\"}"}'
)
FB_POST_HTML = FB_OWN_STORY + FB_RECOMMENDED_STORY


def _write_fb_post_wacz(wacz_path, html: bytes) -> None:
    """A wacz like a crawl of a Facebook post page: the page's html, then bulk-route-definitions requests for its own
    carousel and a recommended post's carousel"""
    from io import BytesIO

    from warcio.statusandheaders import StatusAndHeaders
    from warcio.warcwriter import WARCWriter

    warc = BytesIO()
    writer = WARCWriter(warc, gzip=True)
    writer.write_record(
        writer.create_warc_record(
            FB_POST_URL,
            "response",
            payload=BytesIO(html),
            http_headers=StatusAndHeaders("200 OK", [("Content-Type", 'text/html; charset="utf-8"')], protocol="HTTP/1.1"),
        )
    )
    for fb_id, set_id in [("1646726095764730", "1646726145764725"), ("2172007990360001", "1424364323199649")]:
        body = f"route_urls[0]=%2Fphoto%2F%3Ffbid%3D{fb_id}%26set%3Dpcb.{set_id}%26__tn__%3DEH-R".encode()
        writer.write_record(
            writer.create_warc_record(
                "https://www.facebook.com/ajax/bulk-route-definitions/",
                "request",
                payload=BytesIO(body),
                http_headers=StatusAndHeaders("POST /ajax/bulk-route-definitions/ HTTP/1.1", [], is_http_request=True),
            )
        )
    with ZipFile(wacz_path, "w") as zf:
        zf.writestr("archive/data.warc.gz", warc.getvalue())


@pytest.mark.parametrize(
    "html, crawled_set_ids",
    [
        # only the post's own carousel, not the recommended post's
        (FB_POST_HTML, ["1646726145764725"]),
        # page html without stories - crawl every carousel found, as before
        (b"<html></html>", ["1424364323199649", "1646726145764725"]),
    ],
)
def test_facebook_crawls_only_the_posts_own_carousel(wacz_enricher, tmp_path, mocker, html, crawled_set_ids) -> None:
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    mocker.patch("os.path.expanduser", return_value=str(tmp_path / "aatmp"))
    crawl = mocker.patch.object(wacz_enricher, "save_images_to_enrich_object_from_url_using_browsertrix", return_value=[])
    wacz_file = tmp_path / "fb.wacz"
    _write_fb_post_wacz(wacz_file, html)

    metadata = Metadata().set_url(FB_POST_URL)
    wacz_enricher.facebook_extract_media_from_wacz(metadata, str(wacz_file), FB_POST_URL)

    assert sorted(c.args[3] for c in crawl.call_args_list) == crawled_set_ids


def test_fb_own_post_id_skips_recommended_posts(tmp_path) -> None:
    from auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher import WaczExtractorEnricher

    # recommended post first in the html
    wacz_file = tmp_path / "fb.wacz"
    _write_fb_post_wacz(wacz_file, FB_RECOMMENDED_STORY + FB_OWN_STORY)
    with ZipFile(wacz_file) as zf:
        zf.extract("archive/data.warc.gz", tmp_path)
    warc = str(tmp_path / "archive" / "data.warc.gz")

    assert WaczExtractorEnricher._fb_own_post_id(warc, FB_POST_URL) == "1646726145764725"
    assert WaczExtractorEnricher._fb_own_post_id(warc, "https://www.facebook.com/other") is None


@pytest.mark.parametrize(
    "route, page_html, ocr_phrases, summary",
    [
        # logged out crawl of a photo page showing a login wall
        (
            True,
            FB_PHOTO_HTML,
            ["log in to facebook", "create new account"],
            "page=photo session=LOGGED_OUT screenshot_ocr=log_in_to_facebook,create_new_account strategy=photo_own_image media_added=2 unique_media=2",
        ),
        # a page without photo routes (eg a video) - no requests to tell the session from, and no carousel
        (False, None, [], "page=post session=unknown screenshot_ocr=ok strategy=no_carousel_found media_added=1 unique_media=1"),
    ],
)
def test_facebook_summary_line(wacz_enricher, tmp_path, mocker, route, page_html, ocr_phrases, summary) -> None:
    """one line per FB url of the session, screenshot OCR and strategy, to diagnose prod from the logs"""
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    mocker.patch("os.path.expanduser", return_value=str(tmp_path / "aatmp"))
    ocr = mocker.patch(
        "auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher.check_screenshot_for_facebook_issues",
        return_value=ocr_phrases,
    )
    mock_info = mocker.patch("auto_archiver.utils.custom_logger.logger.info")
    mock_warning = mocker.patch("auto_archiver.utils.custom_logger.logger.warning")
    url = FB_PHOTO_URL if route else "https://www.facebook.com/djhmateer/videos/763070998370197"
    wacz_file = tmp_path / "fb.wacz"
    _write_fb_photo_wacz(wacz_file, FB_PHOTO_IMAGE_URL, route=route, page_html=page_html)

    wacz_enricher.facebook_extract_media_from_wacz(Metadata().set_url(url), str(wacz_file), url)

    assert ocr.call_args[0][0].endswith("browsertrix-screenshot-0.png")
    summaries = [c[0][0] for c in mock_info.call_args_list if c[0][0].startswith("FB summary")]
    assert len(summaries) == 1
    assert summaries[0].startswith(f"FB summary {url} {summary} seconds=")
    warnings = " ".join(c[0][0] for c in mock_warning.call_args_list)
    assert ("logged out" in warnings) == route
    assert ("login phrases" in warnings) == bool(ocr_phrases)


def test_facebook_summary_counts_unique_media(wacz_enricher, tmp_path, mocker) -> None:
    """a walk adds the same photo from more than one photo page, so the summary counts unique content too"""
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    mocker.patch("os.path.expanduser", return_value=str(tmp_path / "aatmp"))

    def crawl(url_build, to_enrich, current_fb_id, set_id):
        for name in ["a.jpg", "b.jpg"]:
            (tmp_path / name).write_bytes(b"same photo")
            to_enrich.add_media(Media(filename=str(tmp_path / name)), name)
        return []

    mocker.patch.object(wacz_enricher, "save_images_to_enrich_object_from_url_using_browsertrix", side_effect=crawl)
    mock_info = mocker.patch("auto_archiver.utils.custom_logger.logger.info")
    wacz_file = tmp_path / "fb.wacz"
    _write_fb_post_wacz(wacz_file, FB_POST_HTML)

    wacz_enricher.facebook_extract_media_from_wacz(Metadata().set_url(FB_POST_URL), str(wacz_file), FB_POST_URL)

    summary = next(c[0][0] for c in mock_info.call_args_list if c[0][0].startswith("FB summary"))
    assert "strategy=own_carousel" in summary
    assert "media_added=2 unique_media=1" in summary
