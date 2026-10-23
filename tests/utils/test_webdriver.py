import pytest
from selenium.common import exceptions as selenium_exceptions

from auto_archiver.utils.webdriver import COOKIE_BUTTON_XPATHS, CookieSettingDriver


class FakeElement:
    def __init__(self, name, displayed=True, enabled=True, stale=False):
        self.name = name
        self.displayed = displayed
        self.enabled = enabled
        self.stale = stale

    def is_displayed(self):
        if self.stale:
            raise selenium_exceptions.StaleElementReferenceException()
        return self.displayed

    def is_enabled(self):
        return self.enabled


class FakeDriver:
    def __init__(self, elements_by_text_index):
        self.elements_by_xpath = {COOKIE_BUTTON_XPATHS[i]: els for i, els in elements_by_text_index.items()}

    def find_elements(self, by, xpath):
        return self.elements_by_xpath.get(xpath, [])


def test_no_banner_returns_false():
    assert CookieSettingDriver._find_clickable_cookie_button(FakeDriver({})) is False


def test_prefers_reject_over_accept():
    accept = FakeElement("accept all")
    reject = FakeElement("reject all")
    # index 3 is "Reject all", index 4 is "Accept all cookies"
    driver = FakeDriver({4: [accept], 3: [reject]})
    assert CookieSettingDriver._find_clickable_cookie_button(driver) is reject


def test_skips_hidden_disabled_and_stale_elements():
    hidden_script = FakeElement("tiktok-cookie-banner-config script", displayed=False)
    disabled = FakeElement("disabled", enabled=False)
    stale = FakeElement("stale", stale=True)
    visible = FakeElement("accept all")
    driver = FakeDriver({0: [hidden_script, disabled, stale], 4: [visible]})
    assert CookieSettingDriver._find_clickable_cookie_button(driver) is visible


def test_only_hidden_matches_returns_false():
    driver = FakeDriver({4: [FakeElement("hidden", displayed=False)]})
    assert CookieSettingDriver._find_clickable_cookie_button(driver) is False


def _startup_timeout():
    import urllib3

    return urllib3.exceptions.ReadTimeoutError(None, "/session", "Read timed out. (read timeout=120)")


def test_firefox_startup_timeout_is_retried(mocker):
    from auto_archiver.utils import webdriver as wd

    mocker.patch.object(wd.time, "sleep")
    driver = mocker.MagicMock()
    mock_cls = mocker.patch.object(wd, "CookieSettingDriver", side_effect=[_startup_timeout(), driver])

    result = wd.Webdriver(1280, 1024, 60).__enter__()

    assert result is driver
    assert mock_cls.call_count == 2
    driver.set_window_size.assert_called_once_with(1280, 1024)


def test_firefox_startup_timeout_raises_after_last_attempt(mocker):
    import pytest
    import urllib3
    from auto_archiver.utils import webdriver as wd

    mocker.patch.object(wd.time, "sleep")
    mock_cls = mocker.patch.object(
        wd, "CookieSettingDriver", side_effect=[_startup_timeout() for _ in range(wd.STARTUP_ATTEMPTS)]
    )

    with pytest.raises(urllib3.exceptions.ReadTimeoutError):
        wd.Webdriver(1280, 1024, 60).__enter__()
    assert mock_cls.call_count == wd.STARTUP_ATTEMPTS


def test_exit_quits_and_kills_leftover_firefox(mocker):
    from auto_archiver.utils import webdriver as wd

    driver = mocker.MagicMock()
    driver.capabilities = {"moz:processID": 12345}
    mocker.patch.object(wd, "CookieSettingDriver", return_value=driver)
    mock_kill = mocker.patch.object(wd.os, "kill", side_effect=ProcessLookupError)

    with wd.Webdriver(1280, 1024, 60):
        pass

    driver.close.assert_not_called()
    driver.quit.assert_called_once()
    mock_kill.assert_called_once_with(12345, wd.signal.SIGKILL)


def test_exit_still_kills_firefox_when_geckodriver_shutdown_times_out(mocker):
    from auto_archiver.utils import webdriver as wd

    driver = mocker.MagicMock()
    driver.capabilities = {"moz:processID": 12345}
    driver.quit.side_effect = TimeoutError("timed out")
    mocker.patch.object(wd, "CookieSettingDriver", return_value=driver)
    mock_kill = mocker.patch.object(wd.os, "kill")
    previous_timeout = wd.socket.getdefaulttimeout()

    with wd.Webdriver(1280, 1024, 60):
        pass

    mock_kill.assert_called_once_with(12345, wd.signal.SIGKILL)
    assert wd.socket.getdefaulttimeout() == previous_timeout


def test_selenium_timeout_is_not_retried(mocker):
    from auto_archiver.utils import webdriver as wd

    mock_cls = mocker.patch.object(
        wd, "CookieSettingDriver", side_effect=selenium_exceptions.TimeoutException("page load")
    )

    assert wd.Webdriver(1280, 1024, 60).__enter__() is None
    assert mock_cls.call_count == 1


class ClickableElement(FakeElement):
    clicked = False

    def click(self):
        self.clicked = True


class FakeBbcDriver(FakeDriver):
    """a page with the BBC's sign in prompt, whose "Maybe later" link closes it"""

    def __init__(self, maybe_later):
        from auto_archiver.utils.webdriver import BBC_SIGN_IN_PROMPT_CLOSE_XPATHS

        self.elements_by_xpath = {BBC_SIGN_IN_PROMPT_CLOSE_XPATHS[0]: maybe_later}
        self._find_clickable_bbc_sign_in_prompt_close = CookieSettingDriver._find_clickable_bbc_sign_in_prompt_close


@pytest.mark.parametrize(
    "url, is_bbc",
    [
        ("https://www.bbc.co.uk/news/articles/c8ryekj1m1do", True),
        ("https://www.bbc.com/news/articles/c8ryekj1m1do", True),
        ("https://www.notbbc.com/news", False),
        ("https://www.aljazeera.com/news", False),
    ],
)
def test_is_bbc_url(url, is_bbc):
    assert CookieSettingDriver._is_bbc_url(url) is is_bbc


def test_closes_bbc_sign_in_prompt(mocker):
    mocker.patch("time.sleep")
    maybe_later = ClickableElement("maybe later")
    driver = FakeBbcDriver([ClickableElement("hidden", displayed=False), maybe_later])

    CookieSettingDriver._close_bbc_sign_in_prompt(driver)

    assert maybe_later.clicked


def test_no_bbc_sign_in_prompt_is_fine(mocker):
    mocker.patch("time.sleep")
    mocker.patch(
        "auto_archiver.utils.webdriver.WebDriverWait"
    ).return_value.until.side_effect = selenium_exceptions.TimeoutException()

    CookieSettingDriver._close_bbc_sign_in_prompt(FakeBbcDriver([]))


def test_cookie_button_is_not_the_bbc_sign_in_prompt_close():
    driver = FakeBbcDriver([ClickableElement("maybe later")])
    assert CookieSettingDriver._find_clickable_cookie_button(driver) is False


@pytest.mark.parametrize(
    "url, conditions",
    [
        # the sign in prompt is closed, and the cookie banner was already rejected, so no generic search
        (
            "https://www.bbc.co.uk/news/articles/c8ryekj1m1do",
            [CookieSettingDriver._find_clickable_bbc_sign_in_prompt_close],
        ),
        ("https://example.com/article", [CookieSettingDriver._find_clickable_cookie_button]),
    ],
)
def test_bbc_skips_generic_cookie_banner_search(mocker, url, conditions):
    mocker.patch("time.sleep")
    mocker.patch("selenium.webdriver.Firefox.get")
    mocker.patch.object(CookieSettingDriver, "find_element", side_effect=selenium_exceptions.NoSuchElementException())
    wait = mocker.patch("auto_archiver.utils.webdriver.WebDriverWait")
    driver = object.__new__(CookieSettingDriver)
    driver.cookie, driver.cookie_jar, driver.facebook_accept_cookies = None, None, False

    driver.get(url)

    assert [c.args[0] for c in wait.return_value.until.call_args_list] == conditions
