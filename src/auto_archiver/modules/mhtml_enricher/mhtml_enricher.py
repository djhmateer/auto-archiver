import time
import os
from auto_archiver.utils.custom_logger import logger

from auto_archiver.core import Media, Metadata
from auto_archiver.core import Extractor, Enricher
from auto_archiver.utils import random_str

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


class MhtmlEnricher(Enricher, Extractor):
    """
    Saves an mhtml file of the URL
    Note the user has to download the file and can't view it live (intentional Chrome security feature)
    """

    def download(self, item: Metadata) -> Metadata:
        # this new Metadata object is required to avoid duplication
        result = Metadata()
        result.merge(item)
        if self.enrich(result):
            return result.success("mhtml")

    # Use Selenium to generate MHTML
    def enrich(self, to_enrich: Metadata) -> bool:
        url = to_enrich.get_url()
    
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        
        # Enable MHTML saving
        chrome_options.add_experimental_option("prefs", {
            "download.default_directory": self.tmp_dir,
            "download.prompt_for_download": False,
        })
        
        driver = webdriver.Chrome(options=chrome_options)
        try:
            driver.get(url)
            # Wait for page to load
            time.sleep(10)
        
            # Use Chrome DevTools Protocol to save as MHTML
            result = driver.execute_cdp_cmd("Page.captureSnapshot", {
                "format": "mhtml"
            })
        
            mhtml_data = result["data"]
        
            # Save MHTML file
            mhtml_filename = os.path.join(self.tmp_dir, f"page_{random_str(8)}.mhtml")
            with open(mhtml_filename, "w") as f:
                f.write(mhtml_data)
        
            # Add to metadata
            mhtml_media = Media(filename=mhtml_filename)
            mhtml_media.mimetype = "multipart/related"
            to_enrich.add_media(mhtml_media, id="mhtml")
        
            return True
        
        except Exception as e:
            logger.error(f"MHTML generation failed: {e}")
            return False
        finally:
            driver.quit()
        