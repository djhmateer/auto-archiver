import base64

import pytest
from selenium.common.exceptions import TimeoutException

from auto_archiver.core import Metadata, Media
from auto_archiver.modules.screenshot_enricher import ScreenshotEnricher


@pytest.fixture
def mock_selenium_env(mocker):
    """Patches Selenium calls and driver checks in one place."""

    # Patch external dependencies
    mock_which = mocker.patch("shutil.which")
    mock_driver_class = mocker.patch("auto_archiver.utils.webdriver.CookieSettingDriver")
    mock_binary_paths = mocker.patch("selenium.webdriver.common.selenium_manager.SeleniumManager.binary_paths")
    mocker.patch("pathlib.Path.is_file", return_value=True)
    mock_popen = mocker.patch("subprocess.Popen")
    mocker.patch("selenium.webdriver.common.service.Service.is_connectable", return_value=True)
    mock_firefox_options = mocker.patch("selenium.webdriver.FirefoxOptions")

    # Define side effect for `shutil.which`
    def mock_which_side_effect(dep):
        return "/mock/geckodriver" if dep == "geckodriver" else None

    mock_which.side_effect = mock_which_side_effect

    # Mock binary paths
    mock_binary_paths.return_value = {
        "driver_path": "/mock/driver",
        "browser_path": "/mock/browser",
    }
    # Mock `subprocess.Popen`
    mock_proc = mocker.MagicMock()
    mock_proc.poll.return_value = None
    mock_popen.return_value = mock_proc
    # Mock `CookieSettingDriver`
    mock_driver = mocker.MagicMock()
    # window and page height 0, so scroll_through_page stops after one step
    mock_driver.execute_script.return_value = 0
    mock_driver_class.return_value = mock_driver
    # Mock `FirefoxOptions`
    mock_options_instance = mocker.MagicMock()
    mock_firefox_options.return_value = mock_options_instance
    yield mock_driver, mock_driver_class, mock_options_instance


@pytest.fixture
def common_patches(tmp_path, mocker):
    """Patches common utilities used across multiple tests."""
    mocker.patch("auto_archiver.utils.url.is_auth_wall", return_value=False)
    mocker.patch("os.path.join", return_value=str(tmp_path / "test.png"))
    mocker.patch("time.sleep")
    yield


@pytest.fixture
def screenshot_enricher(setup_module, mock_binary_dependencies) -> ScreenshotEnricher:
    configs: dict = {
        "width": 1280,
        "height": 720,
        "timeout": 60,
        "sleep_before_screenshot": 4,
        "http_proxy": "",
        "save_to_pdf": False,
        "print_options": {},
    }
    return setup_module("screenshot_enricher", configs)


@pytest.fixture
def metadata_with_video():
    m = Metadata()
    m.set_url("https://example.com")
    m.add_media(Media(filename="video.mp4").set("id", "video1"))
    return m


def test_enrich_adds_screenshot(
    screenshot_enricher,
    metadata_with_video,
    mock_selenium_env,
    common_patches,
    tmp_path,
):
    mock_driver, mock_driver_class, mock_options_instance = mock_selenium_env
    mock_driver.get_full_page_screenshot_as_png.return_value = _png(720)
    screenshot_enricher.enrich(metadata_with_video)
    mock_driver_class.assert_called_once_with(
        cookie=None,
        cookie_jar=None,
        facebook_accept_cookies=False,
        options=mock_options_instance,
    )
    # Verify the actual calls on the returned mock_driver
    mock_driver.get.assert_called_once_with("https://example.com")
    mock_driver.get_full_page_screenshot_as_png.assert_called_once()
    mock_driver.save_screenshot.assert_not_called()
    # Check that the media was added (2 = original video + screenshot)
    assert len(metadata_with_video.media) == 2
    assert metadata_with_video.media[1].properties.get("id") == "webdriverscreenshot"


@pytest.mark.parametrize(
    "url,is_auth",
    [
        ("https://example.com", False),
        ("https://private.com", True),
    ],
)
def test_enrich_auth_wall(
    screenshot_enricher, metadata_with_video, mock_selenium_env, common_patches, url, is_auth, mocker
):
    # Testing with and without is_auth_wall
    mock_driver, mock_driver_class, _ = mock_selenium_env
    mocker.patch("auto_archiver.utils.url.is_auth_wall", return_value=is_auth)
    metadata_with_video.set_url(url)
    screenshot_enricher.enrich(metadata_with_video)

    if is_auth:
        mock_driver.get.assert_not_called()
        assert len(metadata_with_video.media) == 1
        assert metadata_with_video.media[0].properties.get("id") == "video1"
    else:
        mock_driver.get.assert_called_once_with(url)
        assert len(metadata_with_video.media) == 2
        assert metadata_with_video.media[1].properties.get("id") == "webdriverscreenshot"


def test_skip_authwall_no_cookies(screenshot_enricher, caplog):
    # logged at INFO since DM 3rd Jun 25
    with caplog.at_level("INFO"):
        screenshot_enricher.enrich(Metadata().set_url("https://instagram.com"))
    assert "[SKIP] SCREENSHOT since url" in caplog.text


@pytest.mark.parametrize(
    "auth",
    [
        {"cookie": "cookie"},
        {"cookies_jar": "cookie"},
    ],
)
def test_dont_skip_authwall_with_cookies(screenshot_enricher, caplog, mocker, mock_selenium_env, auth):
    mocker.patch("auto_archiver.utils.url.is_auth_wall", return_value=True)

    # patch the authentication dict:
    screenshot_enricher.authentication = {"example.com": auth}
    with caplog.at_level("WARNING"):
        screenshot_enricher.enrich(Metadata().set_url("https://example.com"))
    assert "[SKIP] SCREENSHOT since url" not in caplog.text


def test_show_warning_wrong_auth_type(screenshot_enricher, caplog, mocker, mock_selenium_env):
    mock_driver, mock_driver_class, _ = mock_selenium_env
    mocker.patch("auto_archiver.utils.url.is_auth_wall", return_value=True)
    screenshot_enricher.authentication = {"example.com": {"username": "user", "password": "pass"}}
    with caplog.at_level("WARNING"):
        screenshot_enricher.enrich(Metadata().set_url("https://example.com"))
    assert "Screenshot enricher only supports cookie-type authentication" in caplog.text


def test_handle_timeout_exception(screenshot_enricher, metadata_with_video, mock_selenium_env, mocker):
    mock_driver, mock_driver_class, mock_options_instance = mock_selenium_env

    mock_driver.get.side_effect = TimeoutException
    # patch the module's logger - custom_logger wraps loguru's (logger.patch), so loguru.logger isn't it
    mock_log = mocker.patch("auto_archiver.modules.screenshot_enricher.screenshot_enricher.logger")
    screenshot_enricher.enrich(metadata_with_video)
    mock_log.warning.assert_called_once_with("TimeoutException loading page for screenshot: https://example.com")
    assert len(metadata_with_video.media) == 1


def test_handle_general_exception(screenshot_enricher, metadata_with_video, mock_selenium_env, mocker):
    """Test proper handling of unexpected general exceptions"""
    mock_driver, mock_driver_class, mock_options_instance = mock_selenium_env
    # Simulate a generic exception when save_screenshot is called
    mock_driver.get.return_value = None
    mock_driver.save_screenshot.side_effect = Exception("Unexpected Error")

    mock_log = mocker.patch("auto_archiver.modules.screenshot_enricher.screenshot_enricher.logger")
    screenshot_enricher.enrich(metadata_with_video)
    # Verify that the exception was logged with the log
    mock_log.error.assert_called_once_with(
        "Got error while loading webdriver for screenshot enricher: Unexpected Error"
    )
    # And no new media was added due to the error
    assert len(metadata_with_video.media) == 1


def test_pdf_creation(mocker, screenshot_enricher, metadata_with_video, mock_selenium_env):
    """Test PDF creation when save_to_pdf is enabled"""
    mock_driver, mock_driver_class, mock_options_instance = mock_selenium_env
    # Override the save_to_pdf option
    screenshot_enricher.save_to_pdf = True
    # Mock the print_page method to return base64-encoded content
    mock_driver.print_page.return_value = base64.b64encode(b"fake_pdf_content").decode("utf-8")
    # Patch functions with mocker
    mocker.patch("os.path.join", side_effect=lambda *args: f"{args[-1]}")
    mocker.patch(
        "auto_archiver.modules.screenshot_enricher.screenshot_enricher.random_str",
        return_value="fixed123",
    )
    mock_open = mocker.patch("builtins.open", new_callable=mocker.mock_open)

    screenshot_enricher.enrich(metadata_with_video)
    # Verify screenshot and PDF creation
    mock_driver.save_screenshot.assert_called_once()
    mock_driver.print_page.assert_called_once_with(mock_driver.print_options)
    # Check that PDF file was opened and written
    mock_open.assert_any_call("pdf_fixed123.pdf", "wb")

    # Ensure both screenshot and PDF were added as media
    assert len(metadata_with_video.media) == 3
    assert metadata_with_video.media[1].properties.get("id") == "webdriverscreenshot"
    assert metadata_with_video.media[2].properties.get("id") == "pdf"


@pytest.fixture(autouse=True)
def cleanup_files(tmp_path):
    yield
    for file in tmp_path.iterdir():
        file.unlink()


def _png(height: int) -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (1280, height), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_takes_full_page_screenshot(screenshot_enricher, metadata_with_video, mock_selenium_env, mocker, tmp_path):
    from PIL import Image

    mock_driver, _, _ = mock_selenium_env
    mock_driver.get_full_page_screenshot_as_png.return_value = _png(4000)
    mocker.patch("auto_archiver.utils.url.is_auth_wall", return_value=False)
    mocker.patch("time.sleep")
    screenshot_enricher.tmp_dir = str(tmp_path)

    screenshot_enricher.enrich(metadata_with_video)

    mock_driver.save_screenshot.assert_not_called()
    assert Image.open(metadata_with_video.media[1].filename).size == (1280, 4000)


def test_full_page_screenshot_is_cut_off_at_max_height(tmp_path, mocker):
    from PIL import Image

    from auto_archiver.modules.screenshot_enricher.screenshot_enricher import (
        FULL_PAGE_MAX_HEIGHT,
        save_full_page_screenshot,
    )

    driver = mocker.MagicMock()
    driver.get_full_page_screenshot_as_png.return_value = _png(FULL_PAGE_MAX_HEIGHT + 5000)
    filename = str(tmp_path / "s.png")

    assert save_full_page_screenshot(driver, filename) is True
    assert Image.open(filename).size == (1280, FULL_PAGE_MAX_HEIGHT)


def test_full_page_screenshot_failure_falls_back_to_the_window(
    screenshot_enricher, metadata_with_video, mock_selenium_env, common_patches, tmp_path
):
    mock_driver, _, _ = mock_selenium_env
    mock_driver.get_full_page_screenshot_as_png.side_effect = Exception("Message: tried to take a screenshot too big")

    screenshot_enricher.enrich(metadata_with_video)

    mock_driver.save_screenshot.assert_called_once_with(str(tmp_path / "test.png"))
    assert metadata_with_video.media[1].properties.get("id") == "webdriverscreenshot"


def _scrolling_driver(mocker, window_height: int, page_heights: list[int]):
    """a driver whose page is page_heights[i] high at the i'th scroll step, eg growing as players load"""
    driver = mocker.MagicMock()
    heights = iter(page_heights)

    def execute_script(script, *args):
        if "innerHeight" in script:
            return window_height
        if "scrollHeight" in script:
            return next(heights)
        return None

    driver.execute_script.side_effect = execute_script
    return driver


def _scrolled_to(driver) -> list[int]:
    return [
        c.args[1] if len(c.args) > 1 else 0 for c in driver.execute_script.call_args_list if "scrollTo" in c.args[0]
    ]


def test_scroll_through_page_scrolls_to_the_bottom_and_back(mocker):
    from auto_archiver.modules.screenshot_enricher.screenshot_enricher import scroll_through_page

    mocker.patch("time.sleep")
    # the page grows from 1500 to 2900 as it loads
    driver = _scrolling_driver(mocker, 1000, [1500, 2900, 2900, 2900])

    scroll_through_page(driver)

    assert _scrolled_to(driver) == [1000, 2000, 3000, 0]


def test_scroll_through_page_stops_at_max_height(mocker):
    from auto_archiver.modules.screenshot_enricher.screenshot_enricher import FULL_PAGE_MAX_HEIGHT, scroll_through_page

    mocker.patch("time.sleep")
    # an endless feed
    driver = _scrolling_driver(mocker, 2000, [10**6] * 100)

    scroll_through_page(driver)

    positions = _scrolled_to(driver)
    assert positions[-2] == FULL_PAGE_MAX_HEIGHT + 1000
    assert positions[-1] == 0


def test_scroll_through_page_failure_is_only_a_warning(mocker):
    from auto_archiver.modules.screenshot_enricher.screenshot_enricher import scroll_through_page

    driver = mocker.MagicMock()
    driver.execute_script.side_effect = Exception("javascript error")
    mock_log = mocker.patch("auto_archiver.modules.screenshot_enricher.screenshot_enricher.logger")

    scroll_through_page(driver)

    mock_log.warning.assert_called_once()


def test_scrolls_before_full_page_screenshot_but_not_for_social_media(
    screenshot_enricher, mock_selenium_env, common_patches, mocker
):
    mock_driver, _, _ = mock_selenium_env
    mock_scroll = mocker.patch("auto_archiver.modules.screenshot_enricher.screenshot_enricher.scroll_through_page")
    mocker.patch(
        "auto_archiver.modules.screenshot_enricher.screenshot_enricher.check_screenshot_for_facebook_issues",
        return_value=[],
    )

    screenshot_enricher.enrich(Metadata().set_url("https://www.aljazeera.com/news/2025/8/31/some-article"))
    mock_scroll.assert_called_once_with(mock_driver)

    screenshot_enricher.enrich(Metadata().set_url("https://www.facebook.com/bellingcat/posts/123"))
    mock_scroll.assert_called_once()


# as idf.il's block page
IMPERVA_ERROR_15 = """<html><head><title>Access denied</title></head><body><div>Access denied</div><h1>Error 15</h1>
<p>www.idf.il</p><h2>What happened?</h2><p>This request was blocked by our security service</p>
<p>Incident ID: 1849000760125870434-522967685484214</p><p>Powered by <a href="https://www.imperva.com/">Imperva</a></p>
</body></html>"""


@pytest.mark.parametrize(
    "page_source, blocked",
    [
        (IMPERVA_ERROR_15, True),
        (
            "<html><body>Request unsuccessful. Incapsula incident ID: 123-456</body></html>",
            True,
        ),
        ("<html><body><h1>The 36th Division Began Preparing</h1></body></html>", False),
        # an article about Imperva isn't its block page
        ("<html><body>Imperva said the attack was blocked by its firewall</body></html>", False),
    ],
)
def test_is_imperva_block_page(mocker, page_source, blocked):
    from auto_archiver.modules.screenshot_enricher.screenshot_enricher import is_imperva_block_page

    assert is_imperva_block_page(mocker.MagicMock(page_source=page_source)) is blocked


def test_imperva_block_page_screenshot_is_kept_but_not_as_the_screenshot(
    screenshot_enricher, metadata_with_video, mock_selenium_env, common_patches
):
    mock_driver, _, _ = mock_selenium_env
    mock_driver.page_source = IMPERVA_ERROR_15

    screenshot_enricher.enrich(metadata_with_video)

    assert metadata_with_video.get_media_by_id("webdriverscreenshot") is None
    assert metadata_with_video.get_media_by_id("webdriverscreenshot_blocked")
    assert metadata_with_video.get_status_notes() == ["screenshot blocked by site"]


@pytest.mark.parametrize(
    "url, infinite_scroll",
    [
        ("https://www.facebook.com/bellingcat/posts/123", True),
        ("https://www.instagram.com/p/DPy3dhQjK0S/", True),
        ("https://x.com/bellingcat/status/123", True),
        ("https://twitter.com/bellingcat/status/123", True),
        ("https://www.tiktok.com/@bellingcat/video/123", True),
        ("https://t.me/bellingcat/123", True),
        ("https://vk.com/wall-123_456", True),
        ("https://m.vk.com/wall-123_456", True),
        ("https://www.youtube.com/watch?v=7NWN3wivxhA", True),
        ("https://m.youtube.com/shorts/7NWN3wivxhA", True),
        ("https://youtu.be/7NWN3wivxhA", True),
        ("https://www.aljazeera.com/news/2025/8/31/some-article", False),
        ("https://www.notyoutube.com/watch?v=7NWN3wivxhA", False),
        ("https://www.dropbox.com/s/abc", False),
    ],
)
def test_is_infinite_scroll_site(url, infinite_scroll):
    from auto_archiver.modules.screenshot_enricher.screenshot_enricher import is_infinite_scroll_site

    assert is_infinite_scroll_site(url) is infinite_scroll


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=7NWN3wivxhA",
        "https://www.facebook.com/bellingcat/posts/123",
        "https://x.com/bellingcat/status/123",
    ],
)
def test_infinite_scroll_site_gets_two_windows_without_scrolling(
    screenshot_enricher, metadata_with_video, mock_selenium_env, mocker, tmp_path, url
):
    from PIL import Image

    mock_driver, _, _ = mock_selenium_env
    # eg youtube's comments and recommended videos
    mock_driver.get_full_page_screenshot_as_png.return_value = _png(16764)
    mocker.patch("auto_archiver.utils.url.is_auth_wall", return_value=False)
    mocker.patch("time.sleep")
    mocker.patch(
        "auto_archiver.modules.screenshot_enricher.screenshot_enricher.check_screenshot_for_facebook_issues",
        return_value=[],
    )
    mock_scroll = mocker.patch("auto_archiver.modules.screenshot_enricher.screenshot_enricher.scroll_through_page")
    screenshot_enricher.tmp_dir = str(tmp_path)
    metadata = Metadata().set_url(url)

    screenshot_enricher.enrich(metadata)

    mock_scroll.assert_not_called()
    mock_driver.save_screenshot.assert_not_called()
    # the fixture's window is 720 high
    assert Image.open(metadata.get_media_by_id("webdriverscreenshot").filename).size == (1280, 2 * 720)
