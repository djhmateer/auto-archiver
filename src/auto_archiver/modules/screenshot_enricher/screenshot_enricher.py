# from loguru import logger
from auto_archiver.utils.custom_logger import logger
import time
import os
import base64
import io
import re
from urllib.parse import urlparse

import pytesseract
from PIL import Image
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By


from auto_archiver.core import Enricher
from auto_archiver.utils import Webdriver, url as UrlUtil, random_str
from auto_archiver.core import Media, Metadata

FACEBOOK_WARNING_PHRASES = [
    "we suspect automated behaviour",
    "we suspect automated behavior",
    "your account has been disabled",
    "log in to facebook",
    "you must log in to continue",
    "confirm that this is your account",
    "we locked your account",
    # DM 30th Sep 26 - logged out login wall, which has none of the phrases above
    "see more on facebook",
    "create new account",
    "forgotten password",
    "forgot password",
]

FACEBOOK_STORY_URL_REGEX = re.compile(r"facebook\.com/stories/", re.IGNORECASE)

# DM 22nd Oct 26 - Imperva's block page, eg idf.il's "Access denied - Error 15 - This request was blocked by our security
# service ... Powered by Imperva", or the older "Request unsuccessful. Incapsula incident ID: ..."
IMPERVA_BLOCK_TEXT = re.compile(r"blocked by our security service|incapsula incident id", re.IGNORECASE)
IMPERVA_NAME = re.compile(r"imperva|incapsula", re.IGNORECASE)


def is_imperva_block_page(driver) -> bool:
    """True if the browser was given Imperva's block page instead of the page"""
    try:
        page_source = driver.page_source
    except Exception as e:
        logger.debug(f"Couldn't read the page source to check for an Imperva block page: {e}")
        return False
    return isinstance(page_source, str) and bool(IMPERVA_BLOCK_TEXT.search(page_source) and IMPERVA_NAME.search(page_source))


# DM 22nd Oct 26 - save_screenshot only captures the window (eg 1280x1911 of a 2048 high one), so take the whole page.
# Capped as a long page or an endless feed can be tens of thousands of px, and Firefox fails over ~32k
# DM 23rd Oct 26 - was 15000, but scrolling/capturing that much of heavy pages (eg Russian news sites) hung geckodriver
FULL_PAGE_MAX_HEIGHT = 5000


# DM 22nd Oct 26 - social media, whose pages scroll forever (eg youtube's comments and recommended videos filled a
# screenshot to the 15000px cap), so a full page screenshot isn't useful. They get the top of the page instead
INFINITE_SCROLL_DOMAINS = (
    "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com", "t.me", "telegram.me", "telegram.org",
    "vk.com", "vk.ru", "youtube.com", "youtu.be",
)
# how many windows high the screenshot of an infinite scrolling site is
INFINITE_SCROLL_SCREENSHOT_WINDOWS = 2


def is_infinite_scroll_site(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith(f".{d}") for d in INFINITE_SCROLL_DOMAINS)
# DM 22nd Oct 26 - time for lazy loaded content to appear at each step when scrolling through the page
SCROLL_PAUSE_SECONDS = 0.5


def scroll_through_page(driver) -> None:
    """scrolls down the page a window at a time, then back to the top, so content which only loads when scrolled to
    (eg an Al Jazeera article's video players, lazy loaded images) is in the full page screenshot"""
    try:
        step = driver.execute_script("return window.innerHeight") or 1000
        position = 0
        while position < FULL_PAGE_MAX_HEIGHT:
            position += step
            driver.execute_script("window.scrollTo(0, arguments[0]);", position)
            time.sleep(SCROLL_PAUSE_SECONDS)
            # the page can grow as it loads, so check its height at each step
            if position >= (driver.execute_script("return document.documentElement.scrollHeight") or 0):
                break
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(SCROLL_PAUSE_SECONDS)
    except Exception as e:
        logger.warning(f"Scrolling through the page before the screenshot failed: {e}")


def save_full_page_screenshot(driver, filename: str, max_height: int = FULL_PAGE_MAX_HEIGHT) -> bool:
    """saves the whole page, cut off at max_height. False if Firefox couldn't take it"""
    try:
        png = driver.get_full_page_screenshot_as_png()
        image = Image.open(io.BytesIO(png))
        if image.height > max_height:
            logger.debug(f"Full page screenshot is {image.height}px high, cutting it off at {max_height}px")
            image.crop((0, 0, image.width, max_height)).save(filename)
        else:
            with open(filename, "wb") as f:
                f.write(png)
        return True
    except Exception as e:
        logger.warning(f"Full page screenshot failed, taking the window only: {e}")
        return False


def check_screenshot_for_facebook_issues(screenshot_file: str) -> list[str]:
    """
    Runs OCR on the screenshot and returns a list of any Facebook warning/login
    phrases detected in the text.
    Am keeping screenshots in secrets/fb-blocked folder
    """
    image = Image.open(screenshot_file)
    text = pytesseract.image_to_string(image).lower()
    logger.debug(f"OCR extracted text from {screenshot_file}: {text[:300]!r}")
    return [phrase for phrase in FACEBOOK_WARNING_PHRASES if phrase in text]


# Uses Firefox in webdriver.py
# Cookies are passed in
class ScreenshotEnricher(Enricher):
    def __init__(self, webdriver_factory=None):
        super().__init__()
        self.webdriver_factory = webdriver_factory or Webdriver

    def enrich(self, to_enrich: Metadata) -> None:
        url = to_enrich.get_url()

        # DM 23rd Sep 26 - facebook_story_extractor takes its own screenshot after clicking through the
        # "Click to view story" placeholder - ours would only show the placeholder, and is another story view
        # from the logged-in account
        if FACEBOOK_STORY_URL_REGEX.search(url):
            logger.debug(f"[SKIP] SCREENSHOT for Facebook story, facebook_story_extractor handles it: {url=}")
            return

        logger.debug(f"Enriching screenshot for {url=}")
        auth = self.auth_for_site(url)

        # screenshot enricher only supports cookie-type auth (selenium)
        has_valid_auth = auth and (auth.get("cookies") or auth.get("cookies_jar") or auth.get("cookie"))

        if UrlUtil.is_auth_wall(url) and not has_valid_auth:
            # DM 3rd Jun 25 - demoting to info as am testing Instagram without a cookie to stop being caught as suspicious. Wacz enricher screenshot works.
            logger.info(f"[SKIP] SCREENSHOT since url is behind AUTH WALL and no login details provided: {url=}")
            if any(auth.get(key) for key in ["username", "password", "api_key", "api_secret"]):
                logger.warning(
                    f"Screenshot enricher only supports cookie-type authentication, you have provided {auth.keys()} which are not supported.\
                               Consider adding 'cookie', 'cookies_file' or 'cookies_from_browser' to your auth for this site."
                )
            return

        with self.webdriver_factory(
            self.width,
            self.height,
            self.timeout,
            facebook_accept_cookies="facebook.com" in url,
            http_proxy=self.http_proxy,
            print_options=self.print_options,
            auth=auth,
        ) as driver:
            try:
                # this goes to webdriver.py which has cookie popup handling
                logger.debug(f"Webdriver navigating to {url}")
                get_started = time.monotonic()
                driver.get(url)
                logger.debug(f"Webdriver.get() for {url} took {time.monotonic() - get_started:.1f}s")
                time.sleep(int(self.sleep_before_screenshot))

                screenshot_file = os.path.join(self.tmp_dir, f"screenshot_{random_str(8)}.png")
                if is_infinite_scroll_site(url):
                    # the top of the page, without scrolling, which would only load more of the feed
                    max_height = INFINITE_SCROLL_SCREENSHOT_WINDOWS * int(self.height)
                    if not save_full_page_screenshot(driver, screenshot_file, max_height):
                        driver.save_screenshot(screenshot_file)
                else:
                    scroll_through_page(driver)
                    if not save_full_page_screenshot(driver, screenshot_file):
                        driver.save_screenshot(screenshot_file)
                logger.debug(f"Saved screenshot to {screenshot_file} and about to add to metadata")
                if is_imperva_block_page(driver):
                    # DM 22nd Oct 26 - kept as evidence, but under another id so it isn't the sheet's screenshot (the
                    # crawler's is used instead, as it often gets through eg with its browser profile)
                    logger.warning(f"Screenshot of {url} is Imperva's 'Access denied' block page, not the page")
                    to_enrich.add_media(Media(filename=screenshot_file), id="webdriverscreenshot_blocked")
                    to_enrich.add_status_note("screenshot blocked by site")
                else:
                    to_enrich.add_media(Media(filename=screenshot_file), id="webdriverscreenshot")

                if "facebook.com" in url:
                    logger.debug(f"Facebook URL detected - about to run image recognition on screenshot {screenshot_file} to check for a login prompt")
                    facebook_phrases_found = check_screenshot_for_facebook_issues(screenshot_file)
                    if facebook_phrases_found:
                        logger.warning(f"Facebook screenshot contains warning/login phrases: {facebook_phrases_found}")
                        to_enrich.status = f"FACEBOOK PROBLEM: {', '.join(facebook_phrases_found)}"
                        # todo think about all stop for facebook? perhaps write back to a central database to not do any more fb archving if this sock puppet has tripped
                    else:
                        logger.debug(f"No Facebook phrases detected in screenshot which are: {FACEBOOK_WARNING_PHRASES}")


                if self.save_to_pdf:
                    pdf_file = os.path.join(self.tmp_dir, f"pdf_{random_str(8)}.pdf")
                    pdf = driver.print_page(driver.print_options)
                    with open(pdf_file, "wb") as f:
                        f.write(base64.b64decode(pdf))
                    to_enrich.add_media(Media(filename=pdf_file), id="pdf")
            except TimeoutException:
                logger.warning(f"TimeoutException loading page for screenshot: {url}")
            except Exception as e:
                logger.error(f"Got error while loading webdriver for screenshot enricher: {e}")
