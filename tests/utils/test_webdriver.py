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


def test_selenium_timeout_is_not_retried(mocker):
    from auto_archiver.utils import webdriver as wd

    mock_cls = mocker.patch.object(
        wd, "CookieSettingDriver", side_effect=selenium_exceptions.TimeoutException("page load")
    )

    assert wd.Webdriver(1280, 1024, 60).__enter__() is None
    assert mock_cls.call_count == 1
