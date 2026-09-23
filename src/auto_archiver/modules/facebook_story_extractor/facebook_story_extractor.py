"""
Extractor for Facebook Stories (facebook.com/stories/<id>).

Media comes from the story bucket in the embedded page JSON (data.bucket.unified_stories_with_notes),
verified against a real logged-in highlight bucket on 23 Sep 2026. Facebook's schema is undocumented
and changes without notice - re-check against a saved page source (driver.page_source) if extraction
stops finding cards.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone

import pytesseract
from PIL import Image
from selenium.common import exceptions as selenium_exceptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from auto_archiver.core import Extractor, Media, Metadata
from auto_archiver.utils import Webdriver, random_str
from auto_archiver.utils.custom_logger import logger

# mirrors the phrase list in screenshot_enricher.py - duplicated rather than imported since
# modules are meant to be independently loadable and this list is small
BLOCK_WARNING_PHRASES = [
    "we suspect automated behaviour",
    "we suspect automated behavior",
    "your account has been disabled",
    "log in to facebook",
    "you must log in to continue",
    "confirm that this is your account",
    "we locked your account",
]

# /stories/<bucket id>[/<story card id>] - the bucket is all of an owner's current stories (or a highlight),
# the optional card id (base64, e.g. UzpfSVND...) narrows it to a single card
STORY_URL_REGEX = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?facebook\.com/stories/(?P<id>\d+)(?:/(?P<card_id>[A-Za-z0-9_=-]+))?", re.IGNORECASE
)


def _find_key(obj, key: str, found: list) -> None:
    if isinstance(obj, dict):
        for k, value in obj.items():
            if k == key:
                found.append(value)
            _find_key(value, key, found)
    elif isinstance(obj, list):
        for value in obj:
            _find_key(value, key, found)


class FacebookStoryExtractor(Extractor):
    valid_url = STORY_URL_REGEX

    def download(self, item: Metadata) -> Metadata | bool:
        url = item.get_url()
        # the orchestrator calls download() on every extractor in turn without checking suitable(), so bail out
        # early for anything that isn't a story rather than spinning up a logged-in browser for it
        if not self.suitable(url):
            return False

        auth = self.auth_for_site(url)
        if not (auth.get("cookie") or auth.get("cookies_jar")):
            logger.error(
                "No Facebook authentication configured - facebook_story_extractor requires a logged-in "
                "session's cookies via the 'authentication' config for facebook.com (use a dedicated "
                "login for this, separate from any other Facebook cookies)"
            )
            return False

        # cookie names only (never values) - c_user/xs being present is a good sign the session is logged in
        cookie_names = sorted({c.name for c in auth.get("cookies_jar") or []})
        logger.debug(
            f"facebook_story_extractor using cookies_file={auth.get('cookies_file')} with {len(cookie_names)} cookies: "
            f"{cookie_names} (logged-in cookies c_user/xs present: {'c_user' in cookie_names and 'xs' in cookie_names})"
        )

        with Webdriver(
            self.width,
            self.height,
            self.timeout,
            facebook_accept_cookies=True,
            http_proxy=self.http_proxy,
            auth=auth,
        ) as driver:
            if driver is None:
                logger.error("Failed to start webdriver for facebook_story_extractor")
                return False

            load_start = time.time()
            try:
                driver.get(url)
            except selenium_exceptions.TimeoutException:
                logger.warning(f"TimeoutException loading Facebook story: {url}")
                return False
            logger.debug(f"facebook_story_extractor loaded story in {time.time() - load_start:.1f}s")

            # dismiss the "You're viewing stories" info dialog if it appears, since it blocks the story from rendering/playing
            self._dismiss_viewing_stories_dialog(driver)
            self._click_to_view_story(driver)
            self._pause_story(driver)

            time.sleep(int(self.sleep_before_capture))

            # a redirect to /login or /checkpoint means the cookies didn't work or the account is flagged
            logger.debug(
                f"facebook_story_extractor after load: current_url={driver.current_url} title={driver.title!r}"
            )
            if any(p in driver.current_url for p in ["/login", "/checkpoint"]):
                logger.warning(
                    f"facebook_story_extractor was redirected to {driver.current_url} - cookies not accepted?"
                )

            screenshot_file = os.path.join(self.tmp_dir, f"fb_story_{random_str(8)}.png")
            try:
                driver.save_screenshot(screenshot_file)
            except selenium_exceptions.WebDriverException as e:
                logger.error(f"Failed to capture story page for OCR/media checks: {e}")
                return False

            ocr_text = pytesseract.image_to_string(Image.open(screenshot_file)).lower()
            logger.debug(f"facebook_story_extractor OCR text ({len(ocr_text)} chars): {ocr_text[:300]!r}")
            blocked_phrases = [phrase for phrase in BLOCK_WARNING_PHRASES if phrase in ocr_text]
            if blocked_phrases:
                logger.error(
                    f"Facebook flagged this session while loading story {url}: {blocked_phrases}"
                    + (" - stopping to avoid making it worse" if self.stop_on_suspected_block else "")
                )
                if self.stop_on_suspected_block:
                    return False

            text_content = self._extract_text(driver, ocr_text)

            result = Metadata()
            result.set_url(url)
            if text_content:
                result.set_content(text_content)
            # the page as seen when archived (first card shown) - "screenshot" id also fills the sheet's screenshot column
            result.add_media(Media(filename=screenshot_file), id="screenshot")

            match = STORY_URL_REGEX.search(url)
            bucket = self._find_story_bucket(driver, match.group("id"))
            if bucket:
                owner = bucket.get("story_bucket_owner") or {}
                result.set("story_owner", owner.get("name"))
                result.set("story_owner_id", owner.get("id"))
                result.set("story_bucket_type", bucket.get("story_bucket_type"))
                result.set_title(bucket.get("name") or owner.get("name") or "")
                card_id = match.group("card_id")
                cards = self._cards_from_bucket(bucket, card_id)
                logger.debug(
                    f"facebook_story_extractor bucket {bucket.get('id')} ({bucket.get('story_bucket_type')}) owner="
                    f"{owner.get('name')!r} has {len(cards)} card(s) to download"
                )
                if card_id and not any(card.get("requested_card") for card in cards):
                    logger.warning(
                        f"facebook_story_extractor requested card {card_id} not found in bucket {bucket.get('id')} "
                        "(expired or deleted?) - archiving the rest of the bucket"
                    )
            else:
                cards = [{"url": u} for u in self._extract_video_srcs_from_dom(driver)]
                logger.warning(
                    f"facebook_story_extractor couldn't find the story bucket JSON for {url} - falling back to "
                    f"{len(cards)} rendered <video> src(s)"
                )

            downloaded_any = False
            for card in cards:
                filename = self.download_from_url(card["url"], verbose=False)
                logger.debug(f"facebook_story_extractor download {'OK' if filename else 'FAILED'}: {card['url'][:150]}")
                if not filename:
                    continue
                media = Media(filename=filename)
                # the original Facebook CDN url, shown in the html output - query string (signing/expiry params) dropped
                media.set("src", card["url"].split("?", 1)[0])
                for key in ["type", "story_card_id", "post_id", "caption", "requested_card"]:
                    if card.get(key):
                        media.set(key, card[key])
                if card.get("creation_time"):
                    media.set("timestamp", datetime.fromtimestamp(card["creation_time"], tz=timezone.utc).isoformat())
                result.add_media(media)
                downloaded_any = True

            if not downloaded_any:
                logger.warning(
                    f"facebook_story_extractor could not find/download raw story media for {url} - "
                    "relying on screenshot_enricher/wacz_extractor_enricher (if configured in the "
                    "pipeline) for a visual record instead"
                )

            return result.success("facebook story")

    def _dismiss_viewing_stories_dialog(self, driver) -> None:
        """
        Facebook shows a "You're viewing stories" info dialog with an OK button over the story (seen on the first
        story view for an account), which blocks the story from rendering/playing until it's clicked.
        """
        xpath = (
            "//div[@role='dialog'][.//*[contains(normalize-space(), 'viewing stories')]]"
            "//*[@role='button'][@aria-label='OK' or .//span[normalize-space()='OK']]"
        )
        try:
            button = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, xpath)))
            button.click()
            logger.debug("facebook_story_extractor dismissed the 'You're viewing stories' dialog")
            time.sleep(1)
        except selenium_exceptions.TimeoutException:
            logger.debug("facebook_story_extractor no 'You're viewing stories' dialog found - continuing")
        except selenium_exceptions.WebDriverException as e:
            logger.warning(f"facebook_story_extractor failed to click OK on the 'You're viewing stories' dialog: {e}")

    def _click_to_view_story(self, driver) -> None:
        """
        After the dialog, the story is a grey "Click to view story" placeholder until clicked (the browser needs a user
        gesture to start playback) - click it so the screenshot/OCR shows the actual story. Only affects the visual
        record; the media itself comes from the page JSON either way.
        """
        xpath = "//*[normalize-space(text())='Click to view story']"
        try:
            WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath))).click()
            logger.debug("facebook_story_extractor clicked the 'Click to view story' placeholder")
        except selenium_exceptions.TimeoutException:
            logger.debug("facebook_story_extractor no 'Click to view story' placeholder found - continuing")
        except selenium_exceptions.WebDriverException as e:
            logger.warning(f"facebook_story_extractor failed to click the 'Click to view story' placeholder: {e}")

    def _pause_story(self, driver) -> None:
        """
        A photo card auto-advances after ~5s, so by the end of sleep_before_capture the player has moved on to the
        next card (black while it loads) and the screenshot misses the requested one - pause it on the current card.
        """
        xpath = "//*[@role='button'][@aria-label='Pause']"
        try:
            WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath))).click()
            logger.debug("facebook_story_extractor paused the story")
        except selenium_exceptions.TimeoutException:
            logger.debug("facebook_story_extractor no story Pause button found - screenshot may show a later card")
        except selenium_exceptions.WebDriverException as e:
            logger.warning(f"facebook_story_extractor failed to pause the story: {e}")

    def _find_story_bucket(self, driver, bucket_id: str) -> dict | None:
        """
        Finds the story bucket (all the cards for one owner/highlight, keyed by the id in the /stories/<id> url)
        in the page's embedded JSON, at data.bucket in the relay payload.
        """
        scripts = driver.find_elements(By.CSS_SELECTOR, "script[type='application/json']")
        for script in scripts:
            raw = script.get_attribute("innerHTML")
            if not raw or "unified_stories_with_notes" not in raw:
                continue
            try:
                buckets = []
                _find_key(json.loads(raw), "bucket", buckets)
            except (json.JSONDecodeError, TypeError):
                continue
            for bucket in buckets:
                if (
                    isinstance(bucket, dict)
                    and bucket.get("id") == bucket_id
                    and "unified_stories_with_notes" in bucket
                ):
                    return bucket
        logger.debug(f"facebook_story_extractor no story bucket {bucket_id} found in {len(scripts)} json script blocks")
        return None

    def _cards_from_bucket(self, bucket: dict, card_id: str | None) -> list[dict]:
        """
        Returns one {url, ...metadata} dict per story card: the full-size image for photos, playable_url for videos.
        Deliberately ignores the thumbnails/blurred/preview images and prefetch uris also in the payload.
        Every card in the bucket is returned even when the url names one (share links almost always do, and the rest
        of the bucket expires within ~24h) - that card is flagged requested_card and put first.
        """
        cards = []
        for edge in (bucket.get("unified_stories_with_notes") or {}).get("edges") or []:
            node = edge.get("node") or {}
            for attachment in node.get("attachments") or []:
                media = attachment.get("media") or {}
                media_type = media.get("__typename")
                if media_type == "Video":
                    media_url = media.get("playable_url_quality_hd") or media.get("playable_url")
                else:
                    media_url = (media.get("image") or {}).get("uri")
                if not media_url:
                    logger.debug(f"facebook_story_extractor no media url for {media_type} card {node.get('id')}")
                    continue
                cards.append(
                    {
                        "url": media_url,
                        "type": media_type,
                        "story_card_id": node.get("id"),
                        "post_id": node.get("post_id"),
                        "creation_time": node.get("creation_time"),
                        "caption": media.get("accessibility_caption"),
                        "requested_card": bool(card_id) and node.get("id") == card_id,
                    }
                )
        # stable sort, so the rest keep their bucket order
        return sorted(cards, key=lambda card: not card["requested_card"])

    def _extract_video_srcs_from_dom(self, driver) -> list[str]:
        """Fallback for when the story bucket JSON isn't found - whatever <video> is rendered in the DOM."""
        found = set()
        for video in driver.find_elements(By.TAG_NAME, "video"):
            for attr_holder in [video, *video.find_elements(By.TAG_NAME, "source")]:
                src = attr_holder.get_attribute("src")
                if src and src.startswith("http"):
                    found.add(src)
        return list(found)

    def _extract_text(self, driver, ocr_text: str) -> str:
        """
        Combines any visible DOM text with OCR text from the screenshot, since story captions
        are often burned into the image rather than exposed as page text/metadata.
        """
        try:
            dom_text = driver.find_element(By.TAG_NAME, "body").text
        except selenium_exceptions.WebDriverException:
            dom_text = ""
        parts = [t for t in [dom_text.strip(), ocr_text.strip()] if t]
        return "\n\n---OCR---\n\n".join(parts)
