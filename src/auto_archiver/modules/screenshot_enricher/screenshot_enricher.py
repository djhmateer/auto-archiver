# from loguru import logger
from auto_archiver.utils.custom_logger import logger
import time
import os
import base64

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
]


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
                driver.get(url)
                time.sleep(int(self.sleep_before_screenshot))

                screenshot_file = os.path.join(self.tmp_dir, f"screenshot_{random_str(8)}.png")
                driver.save_screenshot(screenshot_file)
                logger.debug(f"Saved screenshot to {screenshot_file} and about to add to metadata")
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
                logger.info("TimeoutException loading page for screenshot")
            except Exception as e:
                logger.error(f"Got error while loading webdriver for screenshot enricher: {e}")
