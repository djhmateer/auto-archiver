from loguru import logger
import time, os
import os, shutil, subprocess

from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.by import By

from . import Enricher
from ..utils import Webdriver, UrlUtil, random_str  
from ..core import Media, Metadata, ArchivingContext

class ScreenshotEnricher(Enricher):
    name = "screenshot_enricher"

    @staticmethod
    def configs() -> dict:
        return {
            "width": {"default": 1280, "help": "width of the screenshots"},
            "height": {"default": 720, "help": "height of the screenshots"},
            "timeout": {"default": 60, "help": "timeout for taking the screenshot"},
            "sleep_before_screenshot": {"default": 4, "help": "seconds to wait for the pages to load before taking screenshot"},
            "http_proxy": {"default": "", "help": "http proxy to use for the webdriver, eg http://proxy-user:password@proxy-ip:port"},
        }

    def enrich(self, to_enrich: Metadata) -> None:
        url = to_enrich.get_url()
        if UrlUtil.is_auth_wall(url):
            logger.debug(f"[SKIP] SCREENSHOT since url is behind AUTH WALL: {url=}")
            return

        # DM 16th Oct 2024 - see AA Demo Main for use cases - not significantly better at all.
        # maybe for specifc sites.
        
        # logger.debug("Special codepath using playwright to do a screenshot")
        # # where 1.png etc are saved
        # tmp_dir = ArchivingContext.get_tmp_dir()
        # # command = ["pipenv", "run", "xvfb-run", "python3", "c70playwright_general.py", url, tmp_dir]
        # command = ["pipenv", "run", "xvfb-run", "python3", "c71playwright_general_firefox.py", url, tmp_dir]
                
        # # '/mnt/c/dev/v6-auto-archiver' - where the c21.py file is called
        # working_directory = os.getcwd()
        # # Use subprocess.run to execute the command with the specified working directory
        # sub_result = subprocess.run(command, cwd=working_directory, capture_output=True, text=True)

        # # Print the output and error (if any)
        # logger.debug(f"Playwright Output: {sub_result.stdout}")

        # fn = os.path.join(tmp_dir, f"1.png")
        # m = Media(filename=fn)
        # to_enrich.add_media(m, f"playwright-screenshot")



        # DM keep in old screenshotter for now.
        logger.debug(f"Enriching screenshot for {url=}")
        with Webdriver(self.width, self.height, self.timeout, 'facebook.com' in url, http_proxy=self.http_proxy) as driver:
            try:
                driver.get(url)
                time.sleep(int(self.sleep_before_screenshot))

                # youtube cookie popup
                if 'youtube.com' in url:
                    try:
                        reject_button = driver.find_element(By.CSS_SELECTOR, 'button[aria-label="Reject the use of cookies and other data for the purposes described"]')
                        reject_button.click()
                    except Exception as e:
                        # logger.warning(e)
                        logger.debug("No cookies popup which may be fine")

                time.sleep(int(self.sleep_before_screenshot))
                screenshot_file = os.path.join(ArchivingContext.get_tmp_dir(), f"screenshot_{random_str(8)}.png")
                driver.save_screenshot(screenshot_file)
                to_enrich.add_media(Media(filename=screenshot_file), id="screenshot")
            except TimeoutException:
                logger.info("TimeoutException loading page for screenshot")
            except Exception as e:
                logger.error(f"Got error while loading webdriver for screenshot enricher: {e}")
