"""This Webdriver class acts as a context manager for the selenium webdriver."""

from __future__ import annotations

import os
import time
import re

# import domain_for_url
from urllib.parse import urlparse, urlunparse
from http.cookiejar import MozillaCookieJar

import urllib3
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common import exceptions as selenium_exceptions
from selenium.webdriver.common.print_page_options import PrintOptions
from selenium.webdriver.common.by import By

# from loguru import logger
from auto_archiver.utils.custom_logger import logger

# common cookie banner button texts, in order of preference (reject before accept)
COOKIE_BUTTON_TEXTS = [
    "Refuse non-essential cookies",
    "Decline optional cookies",
    "Reject additional cookies",
    "Reject all",
    "Accept all cookies",
]
COOKIE_BUTTON_XPATHS = [
    f"//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text.lower()}')]"
    for text in COOKIE_BUTTON_TEXTS
]

# how many times to try launching Firefox, and how long to wait between attempts
STARTUP_ATTEMPTS = 2
STARTUP_RETRY_DELAY = 5


class CookieSettingDriver(webdriver.Firefox):
    facebook_accept_cookies: bool
    cookie: str
    cookie_jar: MozillaCookieJar

    def __init__(self, cookie, cookie_jar, facebook_accept_cookies, *args, **kwargs):
        if os.environ.get("RUNNING_IN_DOCKER"):
            # Selenium doesn't support linux-aarch64 driver, we need to set this manually
            kwargs["service"] = webdriver.FirefoxService(executable_path="/usr/local/bin/geckodriver")

        super(CookieSettingDriver, self).__init__(*args, **kwargs)
        self.cookie = cookie
        self.cookie_jar = cookie_jar
        self.facebook_accept_cookies = facebook_accept_cookies

    @staticmethod
    def _find_clickable_cookie_button(driver):
        """WebDriverWait condition: the first visible, enabled cookie button in preference order, or False"""
        for xpath in COOKIE_BUTTON_XPATHS:
            for element in driver.find_elements(By.XPATH, xpath):
                try:
                    if element.is_displayed() and element.is_enabled():
                        return element
                except selenium_exceptions.WebDriverException:
                    # eg stale element if the page re-rendered between find and check
                    continue
        return False

    def get(self, url: str):
        step_started = time.monotonic()
        if self.cookie_jar or self.cookie:
            # set up the driver to make it not 'cookie averse' (needs a context/URL)
            # get the 'robots.txt' file which should be quick and easy
            robots_url = urlunparse(urlparse(url)._replace(path="/robots.txt", query="", fragment=""))
            super(CookieSettingDriver, self).get(robots_url)
            logger.debug(f"CookieSettingDriver: fetching {robots_url=} took {time.monotonic() - step_started:.1f}s")

            step_started = time.monotonic()
            if self.cookie:
                # an explicit cookie is set for this site, use that first
                for cookie in self.cookies.split(";"):
                    for name, value in cookie.split("="):
                        self.driver.add_cookie({"name": name, "value": value})
            elif self.cookie_jar:
                domain = urlparse(url).netloc.removeprefix("www.")
                regex = re.compile(f"(www)?.?{domain}$")
                for cookie in self.cookie_jar:
                    if regex.match(cookie.domain):
                        try:
                            self.add_cookie(
                                {
                                    "name": cookie.name,
                                    "value": cookie.value,
                                    "path": cookie.path,
                                    "domain": cookie.domain,
                                    "secure": bool(cookie.secure),
                                    "expiry": cookie.expires,
                                }
                            )
                        except Exception as e:
                            logger.warning(f"Failed to add cookie ({cookie.domain}) to webdriver for url {domain}: {e}")
            logger.debug(f"CookieSettingDriver: adding cookies took {time.monotonic() - step_started:.1f}s")

        step_started = time.monotonic()
        super(CookieSettingDriver, self).get(url)
        logger.debug(f"CookieSettingDriver: fetching actual {url=} took {time.monotonic() - step_started:.1f}s")
        time.sleep(2)

        # Try and use some common button text to reject/accept cookies
        for text in [
            "Refuse non-essential cookies",
            "Decline optional cookies",
            "Reject additional cookies",
            "Reject all",
            "Accept all cookies",
        ]:
            try:
                xpath = f"//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text.lower()}')]"
                self.find_element(By.XPATH, xpath).click()
                time.sleep(2)
            except selenium_exceptions.NoSuchElementException:
                # Button text not found, normal code path
                pass
            except Exception as e:
                # Unusual - happens on tiktok.com
                # Element <script id="tiktok-cookie-banner-config" type="application/json"> could not be scrolled into view
                logger.debug(f"Error clicking cookie button, but continuing to take screenshot: {e}")
                pass

        # Youtube cookie popup
        if 'youtu.be' in url:
            try:
                logger.debug("Trying to click youtu.be cookie popup")    
                reject_button = self.find_element(By.CSS_SELECTOR, 'button[aria-label="Reject all"]')
                reject_button.click()
                # wait for popup to close - between 4 and 10 seconds
                # sleep_before_screenshot takes care of this
            except Exception as e:
                logger.debug("No cookies popup which may be fine") 
        elif 'youtube.com'in url:
            try:
                logger.debug("Trying to click youtube cookie popup")    
                reject_button = self.find_element(By.CSS_SELECTOR, 'button[aria-label="Reject the use of cookies and other data for the purposes described"]')
                reject_button.click()
                # wait for popup to close - between 4 and 10 seconds
                # sleep_before_screenshot takes care of this
            except Exception as e:
                logger.debug("No cookies popup which may be fine")

        # now get the actual URL
        # DM 22nd May 2025 - as I'm passing a cookie I don't need this, and it affects post pages with a popup so turn off 
        elif "x.com" in url or "twitter.com" in url:
            # DM 17th Sep 26 - we already pass a logged-in cookie jar for x.com/twitter.com so no
            # consent banner ever appears here; the generic banner search below was burning up to
            # 5 texts * 5s WebDriverWait = 25s waiting for a banner that never shows up.
            logger.debug("X/Twitter URL detected - skipping generic cookie banner search (cookies already provided)")

        elif "tiktok.com" in url:
            # DM 21st Sep 26 - the only text match on tiktok is a hidden <script id="tiktok-cookie-banner-config">
            # which is never clickable, so the generic search below just burned 5 texts * 5s = 25s per url
            logger.debug("TikTok URL detected - skipping generic cookie banner search")

        elif self.facebook_accept_cookies:
            # try and click the 'close' button on the 'login' window to close it
            # try:
            #     xpath = "//div[@role='dialog']//div[@aria-label='Close']"
            #     self.find_element(By.XPATH, xpath).click()
            #     time.sleep(2)
            # except selenium_exceptions.NoSuchElementException:
            #     logger.info("Unable to find the 'close' button on the facebook login window. This is fine if a cookie is passed.")
            #     pass
            pass

        else:
            # for all other sites, try and use some common button text to reject/accept cookies
            # DM 24th Sep 26 - one shared 5s wait for all texts (was 5s per text, so 25s on every site
            # without a banner). Texts are checked in priority order on each poll so reject still beats accept.
            banner_search_started = time.monotonic()
            try:
                WebDriverWait(self, 5).until(self._find_clickable_cookie_button).click()
            except selenium_exceptions.WebDriverException:
                pass
            logger.debug(
                f"CookieSettingDriver: generic cookie banner search for {url=} took {time.monotonic() - banner_search_started:.1f}s"
            )


class Webdriver:
    def __init__(
        self,
        width: int,
        height: int,
        timeout_seconds: int,
        facebook_accept_cookies: bool = False,
        http_proxy: str = "",
        print_options: dict = {},
        auth: dict = {},
    ) -> webdriver:
        self.width = width
        self.height = height
        self.timeout_seconds = timeout_seconds
        self.auth = auth
        self.facebook_accept_cookies = facebook_accept_cookies
        self.http_proxy = http_proxy
        # create and set print options
        self.print_options = PrintOptions()
        for k, v in print_options.items():
            setattr(self.print_options, k, v)

    def __enter__(self) -> webdriver:
        options = webdriver.FirefoxOptions()
        options.add_argument("--headless")
        options.add_argument(f"--proxy-server={self.http_proxy}")
        options.set_preference("network.protocol-handler.external.tg", False)
        # if facebook cookie popup is present, force the browser to English since then it's easier to click the 'Decline optional cookies' option
        if self.facebook_accept_cookies:
            options.add_argument("--lang=en")

        self.driver = None
        # DM 24th Sep 26 - Firefox occasionally hangs on startup and geckodriver's new session request
        # times out after 120s (urllib3 ReadTimeoutError). It's transient - the next launch normally works -
        # so retry once. Selenium quits the session and stops geckodriver itself when startup fails.
        for attempt in range(1, STARTUP_ATTEMPTS + 1):
            try:
                self.driver = CookieSettingDriver(
                    cookie=self.auth.get("cookie"),
                    cookie_jar=self.auth.get("cookies_jar"),
                    facebook_accept_cookies=self.facebook_accept_cookies,
                    options=options,
                )
                self.driver.set_window_size(self.width, self.height)
                self.driver.set_page_load_timeout(self.timeout_seconds)
                self.driver.print_options = self.print_options
                break
            except urllib3.exceptions.ReadTimeoutError as e:
                if attempt == STARTUP_ATTEMPTS:
                    raise
                logger.warning(
                    f"Firefox did not start (attempt {attempt}/{STARTUP_ATTEMPTS}), retrying in {STARTUP_RETRY_DELAY}s: {e}"
                )
                time.sleep(STARTUP_RETRY_DELAY)
            except selenium_exceptions.TimeoutException as e:
                logger.error(
                    f"failed to get new webdriver, possibly due to insufficient system resources or timeout settings: {e}"
                )
                break

        return self.driver

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.driver is None:
            return True
        # the browser/geckodriver session can already be dead here (crash, connection drop) if
        # something went wrong inside the `with` block, so close()/quit() failing is expected
        # and shouldn't mask or duplicate whatever error the caller already saw
        try:
            self.driver.close()
            self.driver.quit()
        except selenium_exceptions.WebDriverException as e:
            logger.debug(f"Error closing/quitting webdriver, session was likely already dead: {e}")
        del self.driver
        return True
