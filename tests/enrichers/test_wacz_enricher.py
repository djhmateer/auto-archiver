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
    mock_run = mocker.patch(
        "subprocess.run", side_effect=subprocess.CalledProcessError(9, ["docker"], output="", stderr="")
    )
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
        (
            "b'route_urls[0]=%2Fphoto%2F%3Ffbid%3D2439550876400757&routing_namespace'",
            ("2439550876400757", None, False),
            None,
        ),
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
FB_PHOTO_IMAGE_URL = (
    "https://scontent.xx.fbcdn.net/v/t1.6435-9/153394039_1329142914120805_n.jpg?stp=dst-jpg_tt6&_nc_cat=111"
)
FB_STICKER_URL = (
    "https://scontent.xx.fbcdn.net/v/t39.1997-6/12057124_787824104676675_170142219_n.png?stp=dst-png_p240x240"
)
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
                http_headers=StatusAndHeaders(
                    "200 OK", [("Content-Type", 'text/html; charset="utf-8"')], protocol="HTTP/1.1"
                ),
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


FB_POST_ROUTES = [("1646726095764730", "pcb.1646726145764725"), ("2172007990360001", "pcb.1424364323199649")]


def _write_fb_post_wacz(wacz_path, html: bytes, url: str = FB_POST_URL, routes=FB_POST_ROUTES) -> None:
    """A wacz like a crawl of a Facebook post page: the page's html, then bulk-route-definitions requests for its own
    carousel and a recommended post's carousel"""
    from io import BytesIO

    from warcio.statusandheaders import StatusAndHeaders
    from warcio.warcwriter import WARCWriter

    warc = BytesIO()
    writer = WARCWriter(warc, gzip=True)
    writer.write_record(
        writer.create_warc_record(
            url,
            "response",
            payload=BytesIO(html),
            http_headers=StatusAndHeaders(
                "200 OK", [("Content-Type", 'text/html; charset="utf-8"')], protocol="HTTP/1.1"
            ),
        )
    )
    for fb_id, set_value in routes:
        body = f"route_urls[0]=%2Fphoto%2F%3Ffbid%3D{fb_id}%26set%3D{set_value}%26__tn__%3DEH-R".encode()
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
    crawl = mocker.patch.object(
        wacz_enricher, "save_images_to_enrich_object_from_url_using_browsertrix", return_value=[]
    )
    wacz_file = tmp_path / "fb.wacz"
    _write_fb_post_wacz(wacz_file, html)

    metadata = Metadata().set_url(FB_POST_URL)
    wacz_enricher.facebook_extract_media_from_wacz(metadata, str(wacz_file), FB_POST_URL)

    assert sorted(c.args[3] for c in crawl.call_args_list) == crawled_set_ids


FB_SINGLE_PHOTO_ATTACHMENT = b'"attachments":[{"media":{"__typename":"Photo","__isNode":"Photo","id":"1329142910787472"}}]'


@pytest.mark.parametrize(
    "url, html, crawled, srcs, strategy",
    [
        # single photo post - its photo's route is in the author's album, so crawl that photo page once
        (FB_POST_URL, FB_SINGLE_PHOTO_ATTACHMENT, True, [FB_PHOTO_IMAGE_URL], "single_photo"),
        # the album photo is in the post's html but not as its attachment (eg a cover photo) - not crawled
        (FB_POST_URL, b'{"cover_photo":{"photo":{"id":"1329142910787472"}}}', False, [], "no_carousel_found"),
        # the album photo isn't in the post's html - not crawled
        (FB_POST_URL, b"<html></html>", False, [], "no_carousel_found"),
        # a profile's feed - the photo could be any post's, so not crawled
        ("https://www.facebook.com/munigaza", FB_SINGLE_PHOTO_ATTACHMENT, False, [], "no_carousel_found"),
    ],
)
def test_facebook_single_photo_post_crawls_its_photo_page(
    wacz_enricher, tmp_path, mocker, url, html, crawled, srcs, strategy
) -> None:
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    mocker.patch("os.path.expanduser", return_value=str(tmp_path / "aatmp"))
    photo_wacz = tmp_path / "photo.wacz"
    _write_fb_photo_wacz(photo_wacz, FB_PHOTO_IMAGE_URL, page_html=FB_PHOTO_HTML)
    with ZipFile(photo_wacz) as zf:
        zf.extract("archive/data.warc.gz", tmp_path / "photo")
    crawl = mocker.patch.object(
        wacz_enricher, "_fb_browsertrix_crawl", return_value=("c", str(tmp_path / "photo/archive/data.warc.gz"), 1.0)
    )
    mock_info = mocker.patch("auto_archiver.utils.custom_logger.logger.info")
    wacz_file = tmp_path / "fb.wacz"
    _write_fb_post_wacz(wacz_file, html, url, routes=[("1329142910787472", "a.132433247125117")])

    metadata = Metadata().set_url(url)
    wacz_enricher.facebook_extract_media_from_wacz(metadata, str(wacz_file), url)

    assert [c.args[0] for c in crawl.call_args_list] == ([FB_PHOTO_URL] if crawled else [])
    assert [m.get("src") for m in metadata.media if m.get("src")] == srcs
    summary = next(c[0][0] for c in mock_info.call_args_list if c[0][0].startswith("FB summary"))
    assert f"strategy={strategy}" in summary


def test_facebook_single_photo_post_without_photo_node_adds_no_image(wacz_enricher, tmp_path, mocker) -> None:
    """the crawled photo page's html has no photo node - nothing added, not every image passing the size filters
    (the album's other photos)"""
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    mocker.patch("os.path.expanduser", return_value=str(tmp_path / "aatmp"))
    photo_wacz = tmp_path / "photo.wacz"
    _write_fb_photo_wacz(photo_wacz, FB_PHOTO_IMAGE_URL, page_html=b"<html></html>")
    with ZipFile(photo_wacz) as zf:
        zf.extract("archive/data.warc.gz", tmp_path / "photo")
    mocker.patch.object(
        wacz_enricher, "_fb_browsertrix_crawl", return_value=("c", str(tmp_path / "photo/archive/data.warc.gz"), 1.0)
    )
    mock_info = mocker.patch("auto_archiver.utils.custom_logger.logger.info")
    wacz_file = tmp_path / "fb.wacz"
    _write_fb_post_wacz(wacz_file, FB_SINGLE_PHOTO_ATTACHMENT, routes=[("1329142910787472", "a.132433247125117")])

    metadata = Metadata().set_url(FB_POST_URL)
    wacz_enricher.facebook_extract_media_from_wacz(metadata, str(wacz_file), FB_POST_URL)

    assert [m.get("src") for m in metadata.media if m.get("src")] == []
    summary = next(c[0][0] for c in mock_info.call_args_list if c[0][0].startswith("FB summary"))
    assert "strategy=single_photo_not_found" in summary


FB_PROFILE_URL = "https://www.facebook.com/munigaza"
# a profile's feed whose top post is text only, with carousels further down
FB_TEXT_STORY = b'{"post_id":"1450854020483210","attachments":[]}'


@pytest.mark.parametrize(
    "html, crawled_set_ids",
    [
        # top post has no carousel - don't walk the feed's other carousels
        (FB_TEXT_STORY + FB_OWN_STORY, []),
        # top post's own carousel is still crawled
        (FB_OWN_STORY + FB_TEXT_STORY, ["1646726145764725"]),
    ],
)
def test_facebook_profile_doesnt_crawl_the_feeds_carousels(
    wacz_enricher, tmp_path, mocker, html, crawled_set_ids
) -> None:
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    mocker.patch("os.path.expanduser", return_value=str(tmp_path / "aatmp"))
    crawl = mocker.patch.object(
        wacz_enricher, "save_images_to_enrich_object_from_url_using_browsertrix", return_value=[]
    )
    wacz_file = tmp_path / "fb.wacz"
    _write_fb_post_wacz(wacz_file, html, FB_PROFILE_URL)

    metadata = Metadata().set_url(FB_PROFILE_URL)
    wacz_enricher.facebook_extract_media_from_wacz(metadata, str(wacz_file), FB_PROFILE_URL)

    assert sorted(c.args[3] for c in crawl.call_args_list) == crawled_set_ids


@pytest.mark.parametrize(
    "url, is_feed",
    [
        ("https://www.facebook.com/munigaza", True),
        ("https://www.facebook.com/am.fhmy.lwlw/", True),
        ("https://www.facebook.com/profile.php?id=100048268067255", True),
        ("https://www.facebook.com/people/Some-Name/100064103319193/", True),
        ("https://www.facebook.com/groups/767140514330883/", True),
        ("https://www.facebook.com/groups/1039164023827374/user/100041214881321", True),
        (FB_POST_URL, False),
        ("https://www.facebook.com/groups/767140514330883/?multi_permalinks=1165318647846399", False),
        ("https://www.facebook.com/groups/767140514330883/posts/1165318647846399/", False),
        ("https://www.facebook.com/permalink.php?story_fbid=pfbid02Ujkm5&id=100007752185576", False),
        ("https://www.facebook.com/profile.php?story_fbid=123&id=100007752185576", False),
        ("https://www.facebook.com/photo.php?fbid=10159297663446503&set=pb.575936502.-2207520000&type=3", False),
        ("https://www.facebook.com/groups/767140514330883/?view=permalink&id=1165318647846399", False),
        ("https://www.facebook.com/video.php?v=1024036415827926", False),
        ("https://l.facebook.com/l.php?u=https%3A%2F%2Fexample.com", False),
        ("https://www.facebook.com/watch/?v=1024036415827926", False),
        ("https://www.facebook.com/reel/26490289590562050", False),
        ("https://www.facebook.com/share/p/14mZVqfNvkG/", False),
        ("https://www.facebook.com/100003247856309/videos/1306244920862070/", False),
        ("https://www.facebook.com/", False),
    ],
)
def test_fb_is_feed_url(url, is_feed) -> None:
    from auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher import WaczExtractorEnricher

    assert WaczExtractorEnricher._fb_is_feed_url(url) is is_feed


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
        (
            False,
            None,
            [],
            "page=post session=unknown screenshot_ocr=ok strategy=no_carousel_found media_added=1 unique_media=1",
        ),
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


def test_403_not_treated_as_rate_limited(wacz_enricher, mocker):
    """a bot-blocked (403) page is kept (screenshot + WACZ) rather than retried until the crawl fails"""
    mock_run = mocker.patch("subprocess.run")
    wacz_enricher.enrich(Metadata().set_url("https://example.com"))
    cmd = mock_run.call_args[0][0]
    i = cmd.index("--rateLimitStatusCodes")
    assert cmd[i + 1 : i + 3] == ["429", "503"]


def _write_crawl_output(tmp_path, collection, pages):
    import json

    pages_dir = tmp_path / "collections" / collection / "pages"
    pages_dir.mkdir(parents=True)
    (tmp_path / "collections" / collection / f"{collection}.wacz").write_bytes(b"wacz")
    lines = [{"format": "json-pages-1.0", "id": "pages", "title": "Seed Pages"}, *pages]
    (pages_dir / "pages.jsonl").write_text("\n".join(json.dumps(p) for p in lines))


@pytest.mark.parametrize(
    "status,notes",
    [
        (403, ["blocked by site - HTTP 403"]),
        (404, ["site returned HTTP 404"]),
        (200, []),
    ],
)
def test_blocked_seed_page_noted_in_status(wacz_enricher, mocker, tmp_path, status, notes) -> None:
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.use_docker = True
    wacz_enricher.extract_media = wacz_enricher.extract_screenshot = False
    _write_crawl_output(
        tmp_path, wacz_enricher.crawl_id, [{"url": "https://example.com/", "seed": True, "status": status}]
    )
    mocker.patch("subprocess.run")
    metadata = Metadata().set_url("https://example.com/")

    assert wacz_enricher.enrich(metadata) is True
    assert metadata.get_media_by_id("browsertrix")
    assert metadata.get_status_notes() == notes


@pytest.mark.parametrize(
    "status,notes",
    [
        (429, ["blocked by site - HTTP 429 - please requeue"]),
        (503, ["blocked by site - HTTP 503 - please requeue"]),
        (200, ["blocked by site - please requeue"]),  # eg an Imperva page matched by --rateLimitOnMatch
        (403, ["blocked by site - HTTP 403"]),  # an older crawler image still retries 403s
        (None, []),
    ],
)
def test_download_failure_keeps_blocked_note(wacz_enricher, mocker, tmp_path, status, notes) -> None:
    import json
    import subprocess

    wacz_enricher.tmp_dir = str(tmp_path)
    lines = ['{"logLevel":"error","message":"Crawl failed, no pages crawled successfully","details":{}}']
    if status:
        rate_limited = {
            "logLevel": "warn",
            "message": "Page possibly rate limited, retrying",
            "details": {"status": status},
        }
        lines.insert(0, json.dumps(rate_limited))
    stdout = "\n".join(lines)
    mocker.patch("subprocess.run", side_effect=subprocess.CalledProcessError(9, ["docker"], output=stdout, stderr=""))
    metadata = Metadata().set_url("https://example.com")

    assert wacz_enricher.download(metadata) is None
    assert metadata.get_status_notes() == notes


BBC_ARTICLE_URL = "https://www.bbc.co.uk/news/articles/c8ryekj1m1do"
BBC_ARTICLE_IMAGE = "7acf2e90-73d2-11f0-8b59-b1bcdd5e637e"
BBC_OTHER_STORY_IMAGE = "dcf8e630-cd16-11f1-bc2e-018d645d8d21"


def _bbc_image_url(image_id: str, width: int) -> str:
    return f"https://ichef.bbci.co.uk/ace/standard/{width}/cpsprodpb/db61/live/{image_id}.jpg.webp"


BBC_ARTICLE_HTML = f"""<html><body>
<header><a href="/news">News</a></header>
<article><h1>Israel approves plan to take control of Gaza City</h1>
<img src="https://ichef.bbci.co.uk/news/480/cpsprodpb/db61/live/{BBC_ARTICLE_IMAGE}.jpg.webp"
 srcset="https://ichef.bbci.co.uk/news/976/cpsprodpb/db61/live/{BBC_ARTICLE_IMAGE}.jpg.webp 976w">
</article>
<section><h2>Top stories</h2><img src="{_bbc_image_url(BBC_OTHER_STORY_IMAGE, 480)}"></section>
</body></html>""".encode()


def _write_bbc_wacz(wacz_path, html: bytes = None) -> None:
    """A wacz like a crawl of a BBC article: the page's html, the article's image at 2 widths, a top stories
    thumbnail, the video's poster and a site graphic png - all big enough to pass the size filters"""
    from io import BytesIO

    from warcio.statusandheaders import StatusAndHeaders
    from warcio.warcwriter import WARCWriter

    warc = BytesIO()
    writer = WARCWriter(warc, gzip=True)

    def response(url, content_type, payload):
        writer.write_record(
            writer.create_warc_record(
                url,
                "response",
                payload=BytesIO(payload),
                http_headers=StatusAndHeaders("200 OK", [("Content-Type", content_type)], protocol="HTTP/1.1"),
            )
        )

    if html is not None:
        response(BBC_ARTICLE_URL, "text/html; charset=utf-8", html)
    response(_bbc_image_url(BBC_ARTICLE_IMAGE, 480), "image/webp", b"RIFF" + b"0" * 10000)
    response(_bbc_image_url(BBC_ARTICLE_IMAGE, 976), "image/webp", b"RIFF" + b"0" * 30000)
    response(_bbc_image_url(BBC_ARTICLE_IMAGE, 240), "image/webp", b"RIFF" + b"0" * 7000)
    response(_bbc_image_url(BBC_OTHER_STORY_IMAGE, 480), "image/webp", b"RIFF" + b"0" * 20000)
    response("https://ichef.bbci.co.uk/images/ic/1024x576/p0lvsmf0.jpg", "image/jpeg", b"\xff\xd8" + b"0" * 40000)
    response(
        "https://static.files.bbci.co.uk/core/website/assets/static/idcta/news_desktop_context_image.png",
        "image/png",
        b"\x89PNG" + b"0" * 60000,
    )
    with ZipFile(wacz_path, "w") as zf:
        zf.writestr("archive/data.warc.gz", warc.getvalue())


@pytest.mark.parametrize(
    "url, is_bbc",
    [
        (BBC_ARTICLE_URL, True),
        ("https://www.bbc.com/news/articles/c8ryekj1m1do", True),
        ("https://bbc.co.uk/news", True),
        ("https://www.notbbc.com/news", False),
        ("https://www.bbc.co.uk.example.com/news", False),
    ],
)
def test_is_bbc_url(url, is_bbc) -> None:
    from auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher import WaczExtractorEnricher

    assert WaczExtractorEnricher._is_bbc_url(url) is is_bbc


@pytest.mark.parametrize(
    "url, key",
    [
        (_bbc_image_url(BBC_ARTICLE_IMAGE, 240), BBC_ARTICLE_IMAGE),
        (_bbc_image_url(BBC_ARTICLE_IMAGE, 1280), BBC_ARTICLE_IMAGE),
        ("https://ichef.bbci.co.uk/images/ic/1024x576/p0lvsmf0.jpg", "p0lvsmf0"),
        ("https://static.files.bbci.co.uk/core/website/assets/static/idcta/news_desktop_context_image.png", None),
    ],
)
def test_bbc_image_key(url, key) -> None:
    from auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher import WaczExtractorEnricher

    assert WaczExtractorEnricher._bbc_image_key(url) == key


def test_bbc_keeps_only_the_articles_image_at_its_largest(wacz_enricher, tmp_path) -> None:
    """Top stories thumbnails, the video poster and site graphics are dropped, and the article's image is kept once"""
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    wacz_enricher.extract_screenshot = False
    wacz_file = tmp_path / "bbc.wacz"
    _write_bbc_wacz(wacz_file, BBC_ARTICLE_HTML)
    metadata = Metadata().set_url(BBC_ARTICLE_URL)

    wacz_enricher.extract_media_from_wacz(metadata, str(wacz_file))

    assert [m.get("src") for m in metadata.media] == [_bbc_image_url(BBC_ARTICLE_IMAGE, 976)]
    assert os.path.getsize(metadata.media[0].filename) == 30004
    # the 240 width record after it didn't overwrite the kept file
    assert len({m.filename for m in metadata.media}) == 1


def test_bbc_dot_com_url_matches_redirected_co_uk_page(wacz_enricher, tmp_path) -> None:
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    wacz_enricher.extract_screenshot = False
    wacz_file = tmp_path / "bbc.wacz"
    _write_bbc_wacz(wacz_file, BBC_ARTICLE_HTML)
    metadata = Metadata().set_url("https://www.bbc.com/news/articles/c8ryekj1m1do")

    wacz_enricher.extract_media_from_wacz(metadata, str(wacz_file))

    assert [m.get("src") for m in metadata.media] == [_bbc_image_url(BBC_ARTICLE_IMAGE, 976)]


@pytest.mark.parametrize(
    "html",
    [None, b"<html><body><img src='x'></body></html>", b"<html><article></article><article></article></html>"],
)
def test_bbc_without_a_single_article_keeps_all_images(wacz_enricher, tmp_path, html) -> None:
    """No page html in the warc, or no single <article>, falls back to keeping every image passing the size filters"""
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    wacz_enricher.extract_screenshot = False
    wacz_file = tmp_path / "bbc.wacz"
    _write_bbc_wacz(wacz_file, html)
    metadata = Metadata().set_url(BBC_ARTICLE_URL)

    wacz_enricher.extract_media_from_wacz(metadata, str(wacz_file))

    assert len(metadata.media) == 6


YOUTUBE_VIDEO_URL = "https://www.youtube.com/watch?v=7NWN3wivxhA"
YOUTUBE_OWN_THUMBNAIL = "https://i.ytimg.com/vi/7NWN3wivxhA/hqdefault.jpg"
YOUTUBE_SIDEBAR_THUMBNAILS = [
    "https://i.ytimg.com/vi/cAYOrquq2XI/hqdefault.jpg?sqp=-oaymwEnCNACELwBSFryq4qpAxkIARUAAIhCGAHYAQHiAQoIGBACGAY4AUAB&rs=AOn4CLBBPsiJ9YMMfs-6QaA6Z_CHGx5qBQ",
    "https://i.ytimg.com/vi/wKg71lcs5Nw/hqdefault.jpg?v=652bca0d&sqp=-oaymwEnCNACELwBSFryq4qpAxkIARUAAIhCGAHYAQHiAQoIGBACGAY4AUAB&rs=AOn4CLA-RglmV5zf7l4AegTFNaeJXaI-Mw",
]
YOUTUBE_CHANNEL_AVATAR = "https://yt3.ggpht.com/ytc/AIdro_kX4Lz1c2Z0mR0l5w=s176-c-k-c0x00ffffff-no-rj"


def _write_youtube_wacz(wacz_path) -> None:
    """A wacz like a crawl of a YouTube video page: the video's own thumbnail, recommended videos' thumbnails from the
    sidebar and a channel avatar - all big enough to pass the size filters"""
    from io import BytesIO

    from warcio.statusandheaders import StatusAndHeaders
    from warcio.warcwriter import WARCWriter

    warc = BytesIO()
    writer = WARCWriter(warc, gzip=True)

    def response(url, content_type, payload):
        writer.write_record(
            writer.create_warc_record(
                url,
                "response",
                payload=BytesIO(payload),
                http_headers=StatusAndHeaders("200 OK", [("Content-Type", content_type)], protocol="HTTP/1.1"),
            )
        )

    response(YOUTUBE_OWN_THUMBNAIL, "image/jpeg", b"\xff\xd8" + b"0" * 25000)
    for thumbnail in YOUTUBE_SIDEBAR_THUMBNAILS:
        response(thumbnail, "image/avif", b"0" * 30000)
    response(YOUTUBE_CHANNEL_AVATAR, "image/jpeg", b"\xff\xd8" + b"0" * 20000)
    with ZipFile(wacz_path, "w") as zf:
        zf.writestr("archive/data.warc.gz", warc.getvalue())


@pytest.mark.parametrize(
    "url, video_id",
    [
        (YOUTUBE_VIDEO_URL, "7NWN3wivxhA"),
        ("https://youtube.com/watch?feature=share&v=7NWN3wivxhA", "7NWN3wivxhA"),
        ("https://m.youtube.com/watch?v=7NWN3wivxhA&t=10s", "7NWN3wivxhA"),
        ("https://youtu.be/7NWN3wivxhA?si=abc", "7NWN3wivxhA"),
        ("https://www.youtube.com/shorts/7NWN3wivxhA", "7NWN3wivxhA"),
        ("https://www.youtube.com/live/7NWN3wivxhA?feature=shared", "7NWN3wivxhA"),
        ("https://www.youtube.com/@bellingcat", None),
        ("https://www.youtube.com/watch", None),
        ("https://www.notyoutube.com/watch?v=7NWN3wivxhA", None),
    ],
)
def test_youtube_video_id(url, video_id) -> None:
    from auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher import WaczExtractorEnricher

    assert WaczExtractorEnricher._youtube_video_id(url) == video_id


@pytest.mark.parametrize(
    "url, video_id",
    [
        (YOUTUBE_OWN_THUMBNAIL, "7NWN3wivxhA"),
        (YOUTUBE_SIDEBAR_THUMBNAILS[0], "cAYOrquq2XI"),
        ("https://i.ytimg.com/vi_webp/7NWN3wivxhA/maxresdefault.webp", "7NWN3wivxhA"),
        ("https://i9.ytimg.com/an_webp/7NWN3wivxhA/mqdefault_6s.webp", "7NWN3wivxhA"),
        (YOUTUBE_CHANNEL_AVATAR, None),
        ("https://i.ytimg.com/generate_204", None),
    ],
)
def test_ytimg_video_id(url, video_id) -> None:
    from auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher import WaczExtractorEnricher

    assert WaczExtractorEnricher._ytimg_video_id(url) == video_id


def test_youtube_video_skips_other_videos_thumbnails(wacz_enricher, tmp_path) -> None:
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    wacz_enricher.extract_screenshot = False
    wacz_file = tmp_path / "youtube.wacz"
    _write_youtube_wacz(wacz_file)
    metadata = Metadata().set_url(YOUTUBE_VIDEO_URL)

    wacz_enricher.extract_media_from_wacz(metadata, str(wacz_file))

    assert [m.get("src") for m in metadata.media] == [YOUTUBE_OWN_THUMBNAIL, YOUTUBE_CHANNEL_AVATAR]


def test_youtube_page_without_a_video_keeps_all_thumbnails(wacz_enricher, tmp_path) -> None:
    """eg a channel page, where the thumbnails are the content"""
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    wacz_enricher.extract_screenshot = False
    wacz_file = tmp_path / "youtube.wacz"
    _write_youtube_wacz(wacz_file)
    metadata = Metadata().set_url("https://www.youtube.com/@bellingcat/videos")

    wacz_enricher.extract_media_from_wacz(metadata, str(wacz_file))

    assert len(metadata.media) == 4


AJ_ARTICLE_URL = (
    "https://www.aljazeera.com/news/2025/8/31/israel-is-reducing-gaza-city-to-fields-of-rubble-kills-78-across-enclave"
)
AJ_ARTICLE_IMAGE = "/wp-content/uploads/2025/08/reuters_68b342d4-1756578516.jpg"
AJ_OTHER_STORY_IMAGE = "/wp-content/uploads/2026/10/epa_6ad9235a412e-1792615258.jpg"
AJ_VIDEO_THUMBNAIL = (
    "https://cf-images.eu-west-1.prod.boltdns.net/v1/static/665003303001/f1427a5e-3520-425e-a827-e45458f80bcc/"
    "0b29ef9a-8816-4f5f-9019-e854a4222f5a/1920x1080/match/image.jpg"
)


def _aj_image_url(path: str, width: int, height: int) -> str:
    return f"https://www.aljazeera.com{path}?resize={width}%2C{height}&quality=80"


def _aj_html(state: dict | None) -> bytes:
    """the page's html, with its apollo state base64 encoded as on aljazeera.com"""
    import base64
    import json

    script = ""
    if state is not None:
        script = f'<script>window.__APOLLO_STATE__="{base64.b64encode(json.dumps(state).encode()).decode()}";</script>'
    return f"<html><body><main><img src='{_aj_image_url(AJ_ARTICLE_IMAGE, 770, 513)}'></main>{script}</body></html>".encode()


# the page's post, and another story's post as in its most read list
AJ_APOLLO_STATE = {
    "Post:3918307": {
        "__typename": "Post",
        "link": "/news/2025/8/31/israel-is-reducing-gaza-city-to-fields-of-rubble-kills-78-across-enclave",
        "content": '<p>Israel has stepped up</p><video-js data-video-id="6377863811112"></video-js>',
        "featuredImage": {"__ref": f"Media_{AJ_ARTICLE_IMAGE}"},
    },
    "Post:3917235": {
        "__typename": "Post",
        "link": "/news/2026/10/21/another-story",
        "featuredImage": {"__ref": f"Media_{AJ_OTHER_STORY_IMAGE}"},
    },
}


def _write_aj_wacz(wacz_path, html: bytes = None) -> None:
    """A wacz like a crawl of an Al Jazeera article: the page's html, the article's image at 3 sizes, a most read
    story's image, a video's Brightcove thumbnail and the site logo - all big enough to pass the size filters"""
    from io import BytesIO

    from warcio.statusandheaders import StatusAndHeaders
    from warcio.warcwriter import WARCWriter

    warc = BytesIO()
    writer = WARCWriter(warc, gzip=True)

    def response(url, content_type, payload):
        writer.write_record(
            writer.create_warc_record(
                url,
                "response",
                payload=BytesIO(payload),
                http_headers=StatusAndHeaders("200 OK", [("Content-Type", content_type)], protocol="HTTP/1.1"),
            )
        )

    if html is not None:
        response(AJ_ARTICLE_URL, "text/html; charset=utf-8", html)
    response(_aj_image_url(AJ_ARTICLE_IMAGE, 570, 380), "image/jpeg", b"\xff\xd8" + b"0" * 50000)
    response(_aj_image_url(AJ_ARTICLE_IMAGE, 770, 513), "image/webp", b"RIFF" + b"0" * 68000)
    response(_aj_image_url(AJ_ARTICLE_IMAGE, 270, 180), "image/jpeg", b"\xff\xd8" + b"0" * 16000)
    response(_aj_image_url(AJ_OTHER_STORY_IMAGE, 270, 180), "image/jpeg", b"\xff\xd8" + b"0" * 27000)
    response(AJ_VIDEO_THUMBNAIL, "image/jpeg", b"\xff\xd8" + b"0" * 300000)
    response("https://www.aljazeera.com/images/logo_aje.png", "image/png", b"\x89PNG" + b"0" * 60000)
    with ZipFile(wacz_path, "w") as zf:
        zf.writestr("archive/data.warc.gz", warc.getvalue())


@pytest.mark.parametrize(
    "url, is_aljazeera",
    [
        (AJ_ARTICLE_URL, True),
        ("https://balkans.aljazeera.net/videos/2021/11/6/some-video", True),
        ("https://aljazeera.com/news", True),
        ("https://www.notaljazeera.com/news", False),
        ("https://www.aljazeera.com.example.com/news", False),
    ],
)
def test_is_aljazeera_url(url, is_aljazeera) -> None:
    from auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher import WaczExtractorEnricher

    assert WaczExtractorEnricher._is_aljazeera_url(url) is is_aljazeera


@pytest.mark.parametrize(
    "url, key",
    [
        (_aj_image_url(AJ_ARTICLE_IMAGE, 770, 513), AJ_ARTICLE_IMAGE),
        (f"https://www.aljazeera.com{AJ_ARTICLE_IMAGE}", AJ_ARTICLE_IMAGE),
        (AJ_VIDEO_THUMBNAIL, None),
        ("https://www.aljazeera.com/images/logo_aje.png", None),
    ],
)
def test_aljazeera_image_key(url, key) -> None:
    from auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher import WaczExtractorEnricher

    assert WaczExtractorEnricher._aljazeera_image_key(url) == key


def test_aljazeera_keeps_only_the_posts_image_at_its_largest(wacz_enricher, tmp_path) -> None:
    """Other stories' images, the videos' Brightcove thumbnails and site graphics are dropped, and the article's
    image is kept once"""
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    wacz_enricher.extract_screenshot = False
    wacz_file = tmp_path / "aj.wacz"
    _write_aj_wacz(wacz_file, _aj_html(AJ_APOLLO_STATE))
    metadata = Metadata().set_url(AJ_ARTICLE_URL)

    wacz_enricher.extract_media_from_wacz(metadata, str(wacz_file))

    assert [m.get("src") for m in metadata.media] == [_aj_image_url(AJ_ARTICLE_IMAGE, 770, 513)]
    assert os.path.getsize(metadata.media[0].filename) == 68004


@pytest.mark.parametrize("html", [None, _aj_html(None), _aj_html({"Post:1": {"link": "/news/2026/10/21/other"}})])
def test_aljazeera_without_its_post_keeps_all_images(wacz_enricher, tmp_path, html) -> None:
    """No page html in the warc, no apollo state, or no post for the page, falls back to keeping every image"""
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.extract_media = True
    wacz_enricher.extract_screenshot = False
    wacz_file = tmp_path / "aj.wacz"
    _write_aj_wacz(wacz_file, html)
    metadata = Metadata().set_url(AJ_ARTICLE_URL)

    wacz_enricher.extract_media_from_wacz(metadata, str(wacz_file))

    assert len(metadata.media) == 6


IPC_URL = "https://www.ipcinfo.org/ipc-country-analysis/details-map/en/c/1159696/"


def _write_cloudflare_wacz(wacz_path, responses) -> None:
    """A wacz like a crawl of a page behind Cloudflare: responses are (url, status, cf_mitigated) in crawl order"""
    from io import BytesIO

    from warcio.statusandheaders import StatusAndHeaders
    from warcio.warcwriter import WARCWriter

    warc = BytesIO()
    writer = WARCWriter(warc, gzip=True)
    for url, status, cf_mitigated in responses:
        headers = [("Content-Type", "text/html; charset=UTF-8"), ("server", "cloudflare")]
        if cf_mitigated:
            headers.append(("cf-mitigated", cf_mitigated))
        writer.write_record(
            writer.create_warc_record(
                url,
                "response",
                payload=BytesIO(b"<html><title>Just a moment...</title></html>"),
                http_headers=StatusAndHeaders(f"{status} OK", headers, protocol="HTTP/1.1"),
            )
        )
    with ZipFile(wacz_path, "w") as zf:
        zf.writestr("archive/rec-0.warc.gz", warc.getvalue())


@pytest.mark.parametrize(
    "responses, url, challenged",
    [
        ([(IPC_URL, 403, "challenge")], IPC_URL, True),
        # without the trailing slash, or the www
        ([(IPC_URL, 403, "challenge")], "https://ipcinfo.org/ipc-country-analysis/details-map/en/c/1159696", True),
        # the challenge passed and the crawler got the page
        ([(IPC_URL, 403, "challenge"), (IPC_URL, 200, None)], IPC_URL, False),
        # a plain 403, not Cloudflare's challenge
        ([(IPC_URL, 403, None)], IPC_URL, False),
        # a challenge for another of the page's requests
        ([(IPC_URL, 200, None), ("https://www.ipcinfo.org/api/data", 403, "challenge")], IPC_URL, False),
    ],
)
def test_is_cloudflare_challenge(tmp_path, responses, url, challenged) -> None:
    from auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher import WaczExtractorEnricher

    wacz_file = tmp_path / "cf.wacz"
    _write_cloudflare_wacz(wacz_file, responses)

    assert WaczExtractorEnricher._is_cloudflare_challenge(str(wacz_file), url) is challenged


def test_is_cloudflare_challenge_not_a_wacz(tmp_path) -> None:
    from auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher import WaczExtractorEnricher

    (tmp_path / "x.wacz").write_bytes(b"wacz")
    assert WaczExtractorEnricher._is_cloudflare_challenge(str(tmp_path / "x.wacz"), IPC_URL) is False


def _crawl_a_cloudflare_challenge(wacz_enricher, mocker, tmp_path) -> None:
    wacz_enricher.tmp_dir = str(tmp_path)
    wacz_enricher.use_docker = True
    wacz_enricher.extract_media = wacz_enricher.extract_screenshot = False
    collection = wacz_enricher.crawl_id
    _write_crawl_output(
        tmp_path, collection, [{"url": IPC_URL, "seed": True, "status": 403, "title": "Just a moment..."}]
    )
    _write_cloudflare_wacz(tmp_path / "collections" / collection / f"{collection}.wacz", [(IPC_URL, 403, "challenge")])
    mocker.patch("subprocess.run")


def test_cloudflare_challenge_fails_the_enricher(wacz_enricher, mocker, tmp_path) -> None:
    _crawl_a_cloudflare_challenge(wacz_enricher, mocker, tmp_path)
    metadata = Metadata().set_url(IPC_URL)

    assert wacz_enricher.enrich(metadata) is False
    assert metadata.get_media_by_id("browsertrix") is None
    assert metadata.get_title() is None
    assert metadata.get_status_notes() == ["blocked by Cloudflare challenge"]


def test_cloudflare_challenge_fails_the_extractor_keeping_the_note(wacz_enricher, mocker, tmp_path) -> None:
    _crawl_a_cloudflare_challenge(wacz_enricher, mocker, tmp_path)
    item = Metadata().set_url(IPC_URL)

    assert wacz_enricher.download(item) is None
    assert item.get_status_notes() == ["blocked by Cloudflare challenge"]
    assert item.get_context("wacz_failed")


IDF_URL = (
    "https://www.idf.il/en/mini-sites/idf-press-releases-israel-at-war/march-25-pr/"
    "the-36th-division-began-preparing-for-operations-in-the-southern-command/"
)

# as in the page, plus a second embed, a repeat, a nocookie one and a mention of an embed url outside an iframe
IDF_HTML = b"""<html><body><h1>The 36th Division Began Preparing for Operations in the Southern Command</h1>
<p style="text-align: center;"><iframe width="640" height="320" src="https://www.youtube.com/embed/UkI8V8_4OGk"
 title="Preparations of the 36th Division for Operations in the Gaza Strip" frameborder="0"></iframe></p>
<iframe src="//www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0"></iframe>
<iframe src='https://www.youtube.com/embed/UkI8V8_4OGk'></iframe>
<script>var x = "https://www.youtube.com/embed/aaaaaaaaaaa";</script>
</body></html>"""


def _write_page_wacz(wacz_path, url, html: bytes, content_type="text/html; charset=utf-8") -> None:
    from io import BytesIO

    from warcio.statusandheaders import StatusAndHeaders
    from warcio.warcwriter import WARCWriter

    warc = BytesIO()
    writer = WARCWriter(warc, gzip=True)
    writer.write_record(
        writer.create_warc_record(
            url,
            "response",
            payload=BytesIO(html),
            http_headers=StatusAndHeaders("200 OK", [("Content-Type", content_type)], protocol="HTTP/1.1"),
        )
    )
    with ZipFile(wacz_path, "w") as zf:
        zf.writestr("archive/rec-0.warc.gz", warc.getvalue())


def test_youtube_embed_ids(tmp_path) -> None:
    from auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher import WaczExtractorEnricher

    wacz_file = tmp_path / "idf.wacz"
    _write_page_wacz(wacz_file, IDF_URL, IDF_HTML)

    assert WaczExtractorEnricher._youtube_embed_ids(str(wacz_file), IDF_URL) == ["UkI8V8_4OGk", "dQw4w9WgXcQ"]


def test_youtube_embed_ids_not_html(tmp_path) -> None:
    from auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher import WaczExtractorEnricher

    wacz_file = tmp_path / "idf.wacz"
    _write_page_wacz(wacz_file, IDF_URL, IDF_HTML, content_type="application/json")

    assert WaczExtractorEnricher._youtube_embed_ids(str(wacz_file), IDF_URL) == []


def _mock_generic_extractor(wacz_enricher, mocker, tmp_path, fail_ids=()):
    """a generic extractor whose download gives a video named after its id, eg UkI8V8_4OGk.mp4"""

    def download(item):
        video_id = item.get_url().split("v=")[1]
        if video_id in fail_ids:
            raise Exception("Sign in to confirm you're not a bot")
        video = Metadata()
        video.add_media(Media(str(tmp_path / f"{video_id}.mp4")))
        return video

    generic_extractor = mocker.MagicMock()
    generic_extractor.download.side_effect = download
    mocker.patch.object(wacz_enricher, "module_factory").get_module.return_value = generic_extractor
    return generic_extractor


def test_downloads_youtube_embeds(wacz_enricher, mocker, tmp_path) -> None:
    generic_extractor = _mock_generic_extractor(wacz_enricher, mocker, tmp_path)
    wacz_file = tmp_path / "idf.wacz"
    _write_page_wacz(wacz_file, IDF_URL, IDF_HTML)
    metadata = Metadata().set_url(IDF_URL)

    wacz_enricher._download_youtube_embeds(metadata, str(wacz_file), IDF_URL)

    assert [c.args[0].get_url() for c in generic_extractor.download.call_args_list] == [
        "https://www.youtube.com/watch?v=UkI8V8_4OGk",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    ]
    assert [os.path.basename(m.filename) for m in metadata.media] == ["UkI8V8_4OGk.mp4", "dQw4w9WgXcQ.mp4"]


def test_youtube_embeds_skips_videos_already_downloaded_and_failures(wacz_enricher, mocker, tmp_path) -> None:
    generic_extractor = _mock_generic_extractor(wacz_enricher, mocker, tmp_path, fail_ids=("dQw4w9WgXcQ",))
    wacz_file = tmp_path / "idf.wacz"
    _write_page_wacz(wacz_file, IDF_URL, IDF_HTML)
    # eg the generic extractor got the page and its embed
    metadata = Metadata().set_url(IDF_URL)
    metadata.add_media(Media(str(tmp_path / "UkI8V8_4OGk.mp4")))

    wacz_enricher._download_youtube_embeds(metadata, str(wacz_file), IDF_URL)

    assert generic_extractor.download.call_count == 1
    assert [os.path.basename(m.filename) for m in metadata.media] == ["UkI8V8_4OGk.mp4"]


def test_youtube_embeds_capped(wacz_enricher, mocker, tmp_path) -> None:
    from auto_archiver.modules.wacz_extractor_enricher.wacz_extractor_enricher import MAX_EMBEDDED_YOUTUBE_VIDEOS

    generic_extractor = _mock_generic_extractor(wacz_enricher, mocker, tmp_path)
    html = b"".join(b'<iframe src="https://www.youtube.com/embed/video%06d"></iframe>' % i for i in range(8))
    wacz_file = tmp_path / "many.wacz"
    _write_page_wacz(wacz_file, IDF_URL, html)

    wacz_enricher._download_youtube_embeds(Metadata().set_url(IDF_URL), str(wacz_file), IDF_URL)

    assert generic_extractor.download.call_count == MAX_EMBEDDED_YOUTUBE_VIDEOS


def test_youtube_embeds_not_for_a_youtube_page(wacz_enricher, mocker, tmp_path) -> None:
    generic_extractor = _mock_generic_extractor(wacz_enricher, mocker, tmp_path)
    url = "https://www.youtube.com/watch?v=7NWN3wivxhA"
    wacz_file = tmp_path / "yt.wacz"
    _write_page_wacz(wacz_file, url, IDF_HTML)

    wacz_enricher._download_youtube_embeds(Metadata().set_url(url), str(wacz_file), url)

    generic_extractor.download.assert_not_called()
