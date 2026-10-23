import time
import base64
import hashlib
import json
from collections import Counter
import jsonlines
import mimetypes
import os
import re
import shutil
import subprocess
from zipfile import ZipFile
from urllib.parse import parse_qs, urlparse
from bs4 import BeautifulSoup
from auto_archiver.utils.custom_logger import logger
from warcio.archiveiterator import ArchiveIterator

from auto_archiver.core import Media, Metadata
from auto_archiver.core import Extractor, Enricher
from auto_archiver.utils import url as UrlUtil, random_str
from auto_archiver.modules.screenshot_enricher.screenshot_enricher import check_screenshot_for_facebook_issues

LOGIN_PAGE_TITLE = re.compile(r"\b(log ?in|sign ?in)\b", re.IGNORECASE)
NOTIFICATION_COUNT_PREFIX = re.compile(r"^\(\d+\)\s*")
# DM 1st Oct 26 - browsertrix-crawler warnings that show up on most failed (and successful) crawls, so say nothing
# about why the crawl failed - kept out of the one-line failure reason the dashboard shows
CRAWLER_NOISE_MESSAGE = re.compile(r"^(ioredis error|Waiting for redis\b|Page date missing\b)")
# photo route in a bulk-route-definitions request body: fb_id, then the raw set value if there is one, then whether
# another param (%26) follows the set. eg
#   photo%2F%3Ffbid%3D1646726009098072%26set%3Dpcb.1646726145764725%26  - post carousel
#   photo%2F%3Ffbid%3D...%26set%3Dpcb.8610357259060183&routing_namespace=... - post carousel, set ends the route
#   photo%2F%3Ffbid%3D...%26set%3Da.<id>  or  set%3Dpb.<id>.-2207520000  - author's album / profile photos
# DM 14th Oct 26 - the old fixed +13 slice turned a./pb. sets into truncated numeric set_ids (eg 0937538699197),
# and as those links are on every page by the author, the walk crawled the author's album instead of the post
FB_PHOTO_ROUTE = re.compile(r"photo%2F%3Ffbid%3D(\d+)(?:%26set%3D([^%&']*)(%26)?)?")
FB_CAROUSEL_SET = re.compile(r"pcb\.(\d+)")
# DM 15th Oct 26 - stories in a post page's html. A post's carousel set is pcb.<post_id>. Facebook adds recommended
# posts from elsewhere (eg a group post with a car), whose tracking json is marked originated_from_recommendation
FB_POST_ID = re.compile(rb'"post_id":"(\d+)"')
# DM 15th Oct 26 - a /photo page's html has the photo's node, eg "id":"<fbid>",...,"image":{"uri":"<full res url>"}
FB_PHOTO_IMAGE_URI = re.compile(rb'"image":\{"uri":"([^"]+)"')
FB_PHOTO_IMAGE_WINDOW = 3000
# DM 21st Oct 26 - a BBC image's id, the same at every width it is served at, eg
#   https://ichef.bbci.co.uk/ace/standard/480/cpsprodpb/db61/live/7acf2e90-73d2-11f0-8b59-b1bcdd5e637e.jpg.webp
#   https://ichef.bbci.co.uk/images/ic/1024x576/p0lvsmf0.jpg  - video poster
BBC_IMAGE_KEY = re.compile(r"/ic/[^/]+/(p0[a-z0-9]+)|([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
# DM 22nd Oct 26 - a YouTube video's id in its page url and in its thumbnail url, eg
#   https://www.youtube.com/watch?v=7NWN3wivxhA, https://youtu.be/7NWN3wivxhA, https://www.youtube.com/shorts/7NWN3wivxhA
#   https://i.ytimg.com/vi/7NWN3wivxhA/hqdefault.jpg, https://i.ytimg.com/vi_webp/7NWN3wivxhA/maxresdefault.webp
YOUTUBE_PATH_VIDEO_ID = re.compile(r"^/(?:shorts|live|embed|v)/([A-Za-z0-9_-]{11})(?:/|$)")
YTIMG_VIDEO_ID = re.compile(r"^/(?:vi|vi_webp|an|an_webp)/([A-Za-z0-9_-]{11})/")
# DM 22nd Oct 26 - an Al Jazeera image's path, the same at every size it is served at, eg
#   https://www.aljazeera.com/wp-content/uploads/2025/08/reuters_68b342d4-1756578516.jpg?resize=770%2C513&quality=80
AJ_IMAGE_PATH = re.compile(r"/wp-content/uploads/[^?#\"'\s\\]+")
AJ_APOLLO_STATE = re.compile(r'window\.__APOLLO_STATE__\s*=\s*"([^"]+)"')
# DM 22nd Oct 26 - a YouTube video embedded in a page, eg <iframe src="https://www.youtube.com/embed/UkI8V8_4OGk">
YOUTUBE_EMBED_IFRAME = re.compile(
    rb"""<iframe\b[^>]*?\bsrc=["'](?:https?:)?//(?:www\.)?youtube(?:-nocookie)?\.com/embed/([A-Za-z0-9_-]{11})""",
    re.IGNORECASE,
)
# most YouTube videos embedded in a page to download, as each is a full yt-dlp download
MAX_EMBEDDED_YOUTUBE_VIDEOS = 5
# DM 15th Oct 26 - the logged in user id facebook's page sends with its requests, 0 when logged out
FB_SESSION_USER = re.compile(rb"__user=(\d+)")
FB_RECOMMENDED_POST_ID = re.compile(rb'top_level_post_id\\":\\"(\d+)\\"[^}]*?originated_from_recommendation\\":\\"1\\"')
# DM 14th Oct 26 - each crawl adds ~3 files but only ~1 new photo, so cap on crawls not images
FB_WALK_MAX_CRAWLS = 30
# DM 20th Oct 26 - first path segments which are a post, photo, video etc rather than a profile or page's name
# (as are all *.php ones eg video.php, permalink.php - only profile.php can be a profile)
FB_NOT_PROFILE_NAMES = {
    "posts", "videos", "photos", "photo", "watch", "reel", "reels", "share", "stories", "media", "events", "marketplace",
    "hashtag", "search", "notes", "login",
}


def is_bare_site_name(title: str, url: str) -> bool:
    """True for titles like 'Instagram' or '(1) Instagram' on instagram.com - the site name says nothing about the post"""
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return NOTIFICATION_COUNT_PREFIX.sub("", title).strip().lower() in host.split(".")


class WaczExtractorEnricher(Enricher, Extractor):
    """
    Uses https://github.com/webrecorder/browsertrix-crawler to generate a .WACZ archive of the URL
    If used with [profiles](https://github.com/webrecorder/browsertrix-crawler#creating-and-using-browser-profiles)
    it can become quite powerful for archiving private content.
    When used as an archiver it will extract the media from the .WACZ archive so it can be enriched.

    Special case for facebook.com that 
    - gets high resolution images
    - crawls the images if a post contains multiple
    """

    def setup(self) -> None:
        self.use_docker = os.environ.get("WACZ_ENABLE_DOCKER") or not os.environ.get("RUNNING_IN_DOCKER")
        self.docker_in_docker = os.environ.get("WACZ_ENABLE_DOCKER") and os.environ.get("RUNNING_IN_DOCKER")

        self.crawl_id = random_str(8)
        self.cwd_dind = f"/crawls/crawls{self.crawl_id}"
        self.browsertrix_home_host = os.environ.get("BROWSERTRIX_HOME_HOST")
        self.browsertrix_home_container = os.environ.get("BROWSERTRIX_HOME_CONTAINER") or self.browsertrix_home_host
        # create crawls folder if not exists, so it can be safely removed in cleanup
        if self.docker_in_docker:
            os.makedirs(self.cwd_dind, exist_ok=True)

    def cleanup(self) -> None:
        if self.docker_in_docker:
            logger.debug(f"Removing {self.cwd_dind=}")
            shutil.rmtree(self.cwd_dind, ignore_errors=True)

    def download(self, item: Metadata) -> Metadata:
        # this new Metadata object is required to avoid duplication
        result = Metadata()
        result.merge(item)
        if self.enrich(result):
            return result.success("wacz")
        # DM 1st Oct 26 - remember the failure on the orchestrator's item so the enricher step doesn't
        # rerun the same crawl straight away (it fails the same way, and costs 35-70s)
        item.set_context("wacz_failed", True)
        # result is thrown away on failure, so keep any note (eg blocked by site) for the sheet's status
        item.set_context("status_notes", result.get_status_notes())

    def enrich(self, to_enrich: Metadata) -> bool:
        if to_enrich.get_media_by_id("browsertrix"):
            logger.info(f"WACZ enricher had already been executed: {to_enrich.get_media_by_id('browsertrix')}")
            return True

        if to_enrich.get_context("wacz_failed"):
            logger.debug("[SKIP] WACZ since it already failed for this url as an extractor")
            return False

        url = to_enrich.get_url()

        # If url ends with a # then remove it as otherwise browsertrix will fail
        # eg DM 13th Jun 25 - https://www.radiookapi.net/2025/06/12/actualite/securite/au-moins-35-civils-tues-dans-une-attaque-attribuee-aux-adf-banango#
        if url.endswith('#'):
            url = url[:-1]

        collection = self.crawl_id
        browsertrix_home_host = self.browsertrix_home_host or os.path.abspath(self.tmp_dir)
        browsertrix_home_container = self.browsertrix_home_container or browsertrix_home_host

        cmd = [
            "crawl",
            "--url",
            url,
            "--scopeType",
            "page",
            "--generateWACZ",
            "--text",
            "to-pages",
            "--screenshot",
            "fullPage",
            "--collection",
            collection,
            "--id",
            collection,
            "--saveState",
            "never",
            "--behaviors",
            "autoscroll,autoplay,autofetch,siteSpecific",
            "--behaviorTimeout",
            str(self.timeout),
            "--timeout",
            str(self.timeout),
            "--diskUtilization",
            "99",
            # DM 6th Oct 26 - replace browsertrix's default rule (src="/_Incapsula_Resource?:200) which flags every
            # Imperva-fronted page as rate limited, as Imperva injects that script tag into real pages too (eg idf.il).
            # Only Imperva's hard block page now counts; the 403/429/503 status checks are unaffected
            "--rateLimitOnMatch",
            "Incapsula incident ID",
            # DM 19th Oct 26 - drop 403 from browsertrix's default (403, 429, 503), so a bot-blocked page (eg ipcinfo.org
            # behind Imperva) is crawled once and kept (screenshot + WACZ of the block page) rather than retried and the
            # crawl failed. The block is flagged in the status from pages.jsonl. Older images without the flag ignore it
            "--rateLimitStatusCodes",
            "429",
            "503",
            # "--blockAds" # note: this has been known to cause issues on cloudflare protected sites
        ]

        if self.docker_in_docker:
            cmd.extend(["--cwd", self.cwd_dind])

        if self.auth_for_site(url):
            # there's an auth for this site, but browsertrix only supports username/password auth
            if (
                'facebook.com' in url or 
                'twitter.com' in url or 
                'x.com' in url or
                'instagram.com' in url
            ):
                logger.debug(f"Facebook, Twitter/X or Instagram - should be a logged in secrets/profile.tar.gz. This is important for images and screenshot")
            else:
                logger.warning(
                    "The WACZ enricher / Browsertrix does not support using the 'authentication' information for logging in. You should consider creating a Browser Profile for WACZ archiving. More information: https://auto-archiver.readthedocs.io/en/latest/modules/autogen/extractor/wacz_extractor_enricher.html#browsertrix-profiles"
                )

        # call docker if explicitly enabled or we are running on the host (not in docker)
        if self.use_docker:
            logger.debug("Generating WACZ in Docker")
            logger.debug(f"{browsertrix_home_host=} {browsertrix_home_container=}")
            if self.docker_commands:
                cmd = self.docker_commands + cmd
            else:
                cmd = [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{browsertrix_home_host}:/crawls/",
                    "-e",
                    f"GEOMETRY={self.window_size}x16",
                    "webrecorder/browsertrix-crawler",
                ] + cmd

            if self.profile:
                profile_file = f"profile-{self.crawl_id}.tar.gz"
                profile_fn = os.path.join(browsertrix_home_container, profile_file)
                logger.debug(f"Copying {self.profile} to {profile_fn}")
                shutil.copyfile(self.profile, profile_fn)
                cmd.extend(["--profile", os.path.join("/crawls", profile_file)])

        else:
            logger.debug("Generating WACZ without Docker")

            if self.profile:
                cmd.extend(["--profile", os.path.join("/app", str(self.profile))])

        try:
            logger.info(f"Running browsertrix-crawler: {' '.join(cmd)}")
            my_env = os.environ.copy()
            # used when running inside the browsertrix image (not docker), the docker run above passes it with -e
            my_env["GEOMETRY"] = f"{self.window_size}x16"
            if self.proxy_server:
                logger.debug("Using PROXY_SERVER proxy for browsertrix-crawler")
                my_env["PROXY_SERVER"] = self.proxy_server
            elif self.socks_proxy_host and self.socks_proxy_port:
                logger.debug("Using SOCKS proxy for browsertrix-crawler")
                my_env["SOCKS_HOST"] = self.socks_proxy_host
                my_env["SOCKS_PORT"] = str(self.socks_proxy_port)
            # DM 24th Sep 26 - capture crawler output so failures (eg exit status 9) can be diagnosed from our logs
            subprocess.run(cmd, check=True, env=my_env, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            # DM 28th Sep 26 - put the crawler's reason on the first line as the dashboard only shows that
            reason = self._crawler_failure_reason(e.stdout, e.stderr) or "no crawler output"
            logger.error(f"WACZ generation failed (exit status {e.returncode}) for {url}: {reason}")
            if crawler_log := self._summarise_crawler_output(e.stdout, e.stderr):
                logger.error(f"browsertrix-crawler output:\n{crawler_log}")
            if (status := self._crawler_rate_limited_status(e.stdout, e.stderr)) is not None:
                # DM 20th Oct 26 - retried as rate limited (429/503, or 200 for an Imperva page) then failed. Usually
                # temporary (idf.il's Imperva blocks lifted within hours), so ask for a requeue. Not for a 403 (an older
                # crawler image without --rateLimitStatusCodes still retries it), as that block is usually permanent
                if status < 400:
                    to_enrich.add_status_note("blocked by site - please requeue")
                elif status in (429, 503):
                    to_enrich.add_status_note(f"blocked by site - HTTP {status} - please requeue")
                else:
                    to_enrich.add_status_note(f"blocked by site - HTTP {status}")
            return False
        except Exception as e:
            logger.error(f"WACZ generation failed: {e}")
            return False

        if self.docker_in_docker:
            wacz_fn = os.path.join(self.cwd_dind, "collections", collection, f"{collection}.wacz")
        elif self.use_docker:
            wacz_fn = os.path.join(browsertrix_home_container, "collections", collection, f"{collection}.wacz")
        else:
            wacz_fn = os.path.join("collections", collection, f"{collection}.wacz")

        if not os.path.exists(wacz_fn):
            logger.warning(f"Unable to locate and upload WACZ  {wacz_fn=}")
            return False

        # DM 22nd Oct 26 - eg ipcinfo.org, whose every crawl was only of Cloudflare's "Performing security
        # verification" page, yet was recorded as a success. So fail, keeping the reason for the sheet's status
        if self._is_cloudflare_challenge(wacz_fn, url):
            logger.warning(f"WACZ of {url} is only Cloudflare's 'Verify you are human' challenge, not the page - not keeping it")
            to_enrich.add_status_note("blocked by Cloudflare challenge")
            return False

        to_enrich.add_media(Media(wacz_fn), "browsertrix")
        self._download_youtube_embeds(to_enrich, wacz_fn, url)

        # DM 11th Jun 25 - the call to extract_media_from_wacz
        # special case here for facebook.com
        if self.extract_media or self.extract_screenshot:
            if 'facebook.com/' in url:
                self.facebook_extract_media_from_wacz(to_enrich, wacz_fn, url)
            else:
                self.extract_media_from_wacz(to_enrich, wacz_fn)

        if self.docker_in_docker:
            jsonl_fn = os.path.join(self.cwd_dind, "collections", collection, "pages", "pages.jsonl")
        elif self.use_docker:
            jsonl_fn = os.path.join(browsertrix_home_container, "collections", collection, "pages", "pages.jsonl")
        else:
            jsonl_fn = os.path.join("collections", collection, "pages", "pages.jsonl")

        if not os.path.exists(jsonl_fn):
            logger.warning(f"Unable to locate and pages.jsonl  {jsonl_fn=}")
        else:
            logger.info(f"Parsing pages.jsonl  {jsonl_fn=}")
            with jsonlines.open(jsonl_fn) as reader:
                for obj in reader:
                    if "format" in obj:
                        continue  # header line eg {"format":"json-pages-1.0","id":"pages","title":"Seed Pages"}, not a page
                    if obj.get("seed") and (status := obj.get("status") or 0) >= 400:
                        note = f"blocked by site - HTTP {status}" if status in (401, 403, 429) else f"site returned HTTP {status}"
                        logger.warning(f"WACZ of {url} is of the page the site returned instead: {note}")
                        to_enrich.add_status_note(note)
                    if "title" in obj:
                        if to_enrich.get_title() and (
                            LOGIN_PAGE_TITLE.search(obj["title"]) or is_bare_site_name(obj["title"], url)
                        ):
                            # e.g. tiktok shows a login wall in the browser - keep the better title we already have
                            logger.info(f"Keeping title {to_enrich.get_title()!r}, ignoring generic page title {obj['title']!r}")
                        else:
                            to_enrich.set_title(obj["title"])
                    if "text" in obj:
                        to_enrich.set_content(obj["text"])

        return True

    @staticmethod
    def _crawler_failure_reason(stdout: str | None, stderr: str | None) -> str:
        """Distinct warning/error messages from browsertrix-crawler's JSON log output, joined onto one line."""
        messages = []
        for line in f"{stdout or ''}\n{stderr or ''}".splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("logLevel") not in ("warn", "error", "fatal"):
                continue
            message = entry.get("message")
            if message not in messages and not CRAWLER_NOISE_MESSAGE.match(str(message)):
                messages.append(message)
        return "; ".join(messages)

    @staticmethod
    def _crawler_rate_limited_status(stdout: str | None, stderr: str | None) -> int | None:
        """HTTP status of the page browsertrix-crawler retried as rate limited (eg 429, or 200 for a bot check page), or None."""
        for line in f"{stdout or ''}\n{stderr or ''}".splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and str(entry.get("message")).startswith("Page possibly rate limited"):
                return int((entry.get("details") or {}).get("status") or 0)
        return None

    @staticmethod
    def _summarise_crawler_output(stdout: str | None, stderr: str | None, max_lines: int = 20) -> str:
        """
        browsertrix-crawler logs one JSON object per line. Return the warning/error/fatal lines
        (falling back to the tail of the output if there are none), capped at max_lines.
        """
        lines = [line for line in f"{stdout or ''}\n{stderr or ''}".splitlines() if line.strip()]
        problems = [line for line in lines if re.search(r'"logLevel"\s*:\s*"(warn|error|fatal)"', line)]
        return "\n".join((problems or lines)[-max_lines:])

    def facebook_extract_media_from_wacz(self, to_enrich: Metadata, wacz_filename: str, url: str) -> None:
        """
        Facebook specific extractor
        Receives a .wacz archive, and extracts all relevant media from it, adding them to to_enrich.
        Part 1 - extract images from the wacz. Only relevant for /photo (strategy 0) pages. This includes full resolution image so no need to crawl.
        Part 2 - for posts, find the carousel's pcb. set_id and crawl each photo page in it to get the full resolution images
        """
        # DM 15th Oct 26 - one line per FB url of what was found and which strategy ran, to diagnose prod from aa_logs
        # eg WHERE message LIKE '%FB summary%strategy=no_carousel_found%'
        diag = {"page": "photo" if "facebook.com/photo" in url else "post"}
        fb_start = time.perf_counter()
        media_start = len(to_enrich.media)
        try:
            self._facebook_extract_media_from_wacz(to_enrich, wacz_filename, url, diag)
        finally:
            added = to_enrich.media[media_start:]
            diag["media_added"] = len(added)
            # DM 15th Oct 26 - walks add each photo more than once (deduplicated at upload), so count by content too
            diag["unique_media"] = len({self._short_file_hash(m.filename) for m in added})
            diag["seconds"] = round(time.perf_counter() - fb_start)
            logger.info(f"FB summary {url} " + " ".join(f"{k}={v}" for k, v in diag.items()))

    def _facebook_extract_media_from_wacz(self, to_enrich: Metadata, wacz_filename: str, url: str, diag: dict) -> None:
        logger.info(f"Facebook Part 1 - extracting media from {wacz_filename=}")
        fb_start = time.perf_counter()

        # unzipping the .wacz
        tmp_dir = self.tmp_dir
        unzipped_dir = os.path.join(tmp_dir, "unzipped")
        with ZipFile(wacz_filename, "r") as z_obj:
            z_obj.extractall(path=unzipped_dir)

        # if warc is split into multiple gzip chunks, merge those
        warc_dir = os.path.join(unzipped_dir, "archive")
        warc_filename = os.path.join(tmp_dir, "merged.warc")
        with open(warc_filename, "wb") as outfile:
            for filename in sorted(os.listdir(warc_dir)):
                if filename.endswith(".gz"):
                    chunk_file = os.path.join(warc_dir, filename)
                    with open(chunk_file, "rb") as infile:
                        shutil.copyfileobj(infile, outfile)

        # DM 15th Oct 26 - the browsertrix screenshot, for every FB url. This used to run inside the set_id loop below,
        # so was lost when no pcb. set_id was found (eg a video page), and added once per set_id when there were several
        counter_screenshots = self._facebook_extract_screenshots(to_enrich, warc_filename) if self.extract_screenshot else 0
        counter_warc_files = 0

        # DM 15th Oct 26 - a logged out crawl (eg profile.tar.gz has lost its session) gets fewer/no images
        session_user = self._fb_session_user(warc_filename)
        diag["session"] = "unknown" if session_user is None else ("LOGGED_OUT" if session_user == "0" else "logged_in")
        if session_user == "0":
            logger.warning(f"FB browsertrix crawl was logged out (__user=0) for {url} - check the browsertrix profile {self.profile}")

        # DM 15th Oct 26 - login walls / blocks on the browsertrix screenshot, as the screenshot_enricher does for its own
        diag["screenshot_ocr"] = self._fb_screenshot_problems(to_enrich, url) if counter_screenshots else "no_screenshot"

        # DM 15th Oct 26 - Strategy 0 - a /photo page has its single full resolution image in its own wacz, so no set_id
        # or Part 2 crawl is needed. This used to run inside the set_id loop below, so the image was lost when no pcb.
        # set_id was found, eg a photo in the author's album (set=a.<id>)
        if self.extract_media and "facebook.com/photo" in url:
            # DM 15th Oct 26 - keep only the photo's own image, not comment stickers, profile pictures or the album's next
            # photo. If its image isn't found, keep every image which passes the size filters as before
            photo_path = self._fb_photo_image_path(warc_filename, url)
            counter_warc_files = self._facebook_extract_single_photo(to_enrich, warc_filename, photo_path)
            diag["strategy"] = "photo_own_image" if counter_warc_files else "photo_size_filter_fallback"
            if photo_path and counter_warc_files == 0:
                logger.info(f"FB photo image {photo_path=} not in the wacz, so keeping every image which passes the size filters")
                counter_warc_files = self._facebook_extract_single_photo(to_enrich, warc_filename)
            elif not photo_path:
                logger.info(f"FB photo's node not found in the page's html for {url}, so keeping every image which passes the size filters")
        elif not self.extract_media:
            diag["strategy"] = "extract_media_off"

        if not self.extract_media or "facebook.com/photo" in url:
            logger.info(
                f"Facebook WACZ extract_media/extract_screenshot finished, found {counter_warc_files + counter_screenshots} relevant media file(s)"
            )
            logger.info(
                f"FB timing - facebook_extract_media_from_wacz took {time.perf_counter() - fb_start:.0f}s for {url} (no carousel crawl)"
            )
            return

        # using a separate linux_tmp_dir for Part 2 to avoid confusion with the tmp_dir used for Part 1
        linux_tmp_dir = os.path.expanduser('~/aatmp')

        # Check if the directory exists, and if not, create it
        if os.path.exists(linux_tmp_dir):
           logger.debug(f"Part 2 - deleting all files in {linux_tmp_dir}")
           shutil.rmtree(linux_tmp_dir)
           os.makedirs(linux_tmp_dir)
        else:
            logger.debug(f"Part 2 - creating directory {linux_tmp_dir}")
            os.makedirs(linux_tmp_dir)

        # DM 13th Jun 2025
        # for strategy 1 - we need the most prevalent set_id
        # so we crawl the correct set as otherwise we may get wrong images
        album_photo = None  # (fb_id, set_value) of the first photo route in the author's album, for a single photo post
        with open(warc_filename, "rb") as warc_stream:
            list_of_set_ids = []
            for record in ArchiveIterator(warc_stream):
                if record.rec_type == "request": pass
                else: continue 

                uri = record.rec_headers.get_header('WARC-Target-URI')

                if "bulk-route-definitions/" in uri:
                    route = self._first_fb_photo_route(str(record.content_stream().read()))
                    if route:
                        fb_id, set_value, more_params = route
                        set_id = self._fb_carousel_set_id(set_value, more_params)
                        if set_id is None:
                            logger.debug(f"skipping {set_value=} {fb_id=} - not a post carousel (pcb.) set, eg the author's album or profile photos")
                            if album_photo is None and (set_value or "").startswith("a."):
                                album_photo = (fb_id, set_value)
                            continue

                        logger.info(f" found {set_id=} in bulk-route-definitions and adding to list so can calculate most prevalent")
                        # DM 8th Oct 26 - diagnosing ~20 min FB posts: same set_id seen across unrelated posts, so log where it came from
                        logger.debug(f"FB set_id source - {set_id=} {fb_id=} {uri=}")
                        list_of_set_ids.append(set_id)

        if list_of_set_ids: pass
        elif album_photo and not self._fb_is_feed_url(url) and self._fb_post_has_photo(warc_filename, url, album_photo[0]):
            # DM 23rd Oct 26 - a single photo post. Only when the photo is a story's attachment in the page's html, so
            # not eg a cover photo, and not on a profile/page/group feed, where it could be any post's photo
            counter_warc_files = self._facebook_extract_album_photo(to_enrich, *album_photo)
            diag["strategy"] = "single_photo" if counter_warc_files else "single_photo_not_found"
            return
        else:
            logger.info(f"FB no carousel set_ids found in bulk-route-definitions for {url}. Have seen this if content is not available ie page has been taken down")
            logger.debug("Have also seen this if there is a video on the FB page")
            diag["strategy"] = "no_carousel_found"
            return

        # get the most prevalent set_id
        # 6th March 2025 - for the case of 3 images I'm sometimes getting what looks like advertising images
        # which are slightly more prevalent than the images I want.
        # 5 and 3
        # logger.warning(f" {list_of_set_ids=}")
        # most_prevalent_set_id = max(set(list_of_set_ids), key=list_of_set_ids.count)
        # most_prevalent_setid=1646726145764725 should be for 3 images set
        # logger.warning(f" correct set_id is 1646726145764725, calculated: {most_prevalent_set_id=}")
        # logger.warning(f" lowest number in list_of_set_ids is {min(list_of_set_ids, key=lambda x: int(x))}")

        unique_set_ids = list(set(list_of_set_ids))
        logger.info(f"FB set_id counts for {url} - {dict(Counter(list_of_set_ids))}")

        # DM 15th Oct 26 - only crawl the post's own carousel, not ones on recommended posts further down the page.
        # If the post's own one wasn't found, crawl them all as before
        own_post_id = self._fb_own_post_id(warc_filename, url)
        diag["carousels_found"] = dict(Counter(list_of_set_ids))
        diag["own_post_id"] = own_post_id
        diag["strategy"] = "own_carousel" if own_post_id in unique_set_ids else "all_carousels_fallback"
        if own_post_id in unique_set_ids:
            if len(unique_set_ids) > 1:
                logger.info(f"FB only crawling the post's own carousel {own_post_id=}, ignoring {sorted(set(unique_set_ids) - {own_post_id})}")
            unique_set_ids = [own_post_id]
        elif self._fb_is_feed_url(url):
            # DM 20th Oct 26 - a profile, page or group url is a feed of posts, so crawling them all walked every carousel
            # in the feed (munigaza: 7 carousels, 51 crawls, 38 min). Its top post's carousel (above) is still crawled
            logger.info(f"FB {url} is a profile/page/group feed and its top post {own_post_id=} isn't one of its carousels, so not crawling the feed's {len(unique_set_ids)} carousel(s)")
            diag["strategy"] = "feed_carousels_skipped"
            unique_set_ids = []
        else:
            logger.info(f"FB post's own carousel {own_post_id=} not in found set_ids, so crawling them all")
        logger.debug(f" looping over all {unique_set_ids=}")

        for most_prevalent_set_id in unique_set_ids:
            logger.debug(f" processing {most_prevalent_set_id=}")

            with open(warc_filename, "rb") as warc_stream:
                full_crawl_done = False
                for record in ArchiveIterator(warc_stream):
                    if full_crawl_done: break # out of the records for this set_id

                    if record.rec_type == "request": pass
                    else: continue # to next record - we are only interested in the request at the moment to get the fb_id and set_id
    
                    # CRAWL START
                    # logger.debug(f"Strategy 1 + - crawling the sub page to get the media")
    
                    # Get fb_id and set_id
                    # strategy 1 - 3 images and want full resolution images
                    # https://www.facebook.com/khitthitnews/posts/pfbid0PTvT6iAccWqatvbDQNuqpFwL5WKzHuLK4QjP97Fwut637CV3XXQU53z1s2bJMAKwl
    
                    # writing in table image: 
                    # https://www.facebook.com/photo/?fbid=1646726009098072&set=pcb.1646726145764725
                    # fbid = 1646726009098072 
    
                    # set = 1646726145764725
    
                    # crosshairs image
                    # https://www.facebook.com/photo?fbid=1646726045764735&set=pcb.1646726145764725
                    # fbid = 1646726045764735
                    
                    # printout of map image
                    # https://www.facebook.com/photo?fbid=1646726095764730&set=pcb.1646726145764725
                    # fbid = 1646726095764730
                    
                    uri = record.rec_headers.get_header('WARC-Target-URI')
    
                    # There are many instances of this and it is possible to get the wrong one.
                    # DM 13th Jun 25 - we already know the most prevalent set_id that we want
                    if "bulk-route-definitions/" in uri:
                        content = record.content_stream().read()
                        foo = str(content)
    
                        # Strategy 1 - 3 images
                        # photo%2F%3Ffbid%3D1646726009098072%26set%3Dpcb.1646726145764725%26
                        # writing in table image 
                        # fbid = 1646726009098072
                        # set = pcb.1646726145764725
    
                        route = self._first_fb_photo_route(foo)

                        if route:
                            logger.debug("Part 1 - found photo string so get the fb_id and set_id so can request it to get full res image")
                            logger.debug("   and then the next fb_id from the carousel")
                            fb_id, set_value, more_params = route
                            set_id = self._fb_carousel_set_id(set_value, more_params)

                            if set_id is None:
                                logger.debug(f"Part 1 - skipping {set_value=} {fb_id=} - not a post carousel (pcb.) route")
                                continue # to next record
                            if set_id == most_prevalent_set_id: pass
                            else:
                                logger.info(f"Part 1 - skipping set_id {set_id=} as not the set_id being walked {most_prevalent_set_id=}")
                                continue # to next record
    
                            logger.info(f"  *** Part 1 - Strategy 1 {fb_id=} and {set_id=}")
                            # bar = f'https://www.facebook.com/photo/?fbid={fb_id}&set=pcb.{set_id}'
    
                            # Part 2
                            fb_ids_to_request = []
                            fb_ids_requested = []
                            walk_start = time.perf_counter()
                            walk_media_start = len(to_enrich.media)
                            walk_stop_reason = "queue empty"
                            while (True):
                                builder_url = f"https://www.facebook.com/photo?fbid={fb_id}&set=pcb.{set_id}"
    
                                fb_ids_requested.append(fb_id)
    
                                logger.info(f"  *** Part 2 next trying url for js page {builder_url}")
    
                                # next_fb_id = self.save_images_to_enrich_object_from_url_using_browsertrix(builder_url, to_enrich, fb_id)
                                list_of_next_fb_ids = self.save_images_to_enrich_object_from_url_using_browsertrix(builder_url, to_enrich, fb_id, set_id)
    
                                # DM 8th Oct 26 - log why the queue doesn't shrink (both lists are now checked before queueing)
                                already_requested = [x for x in list_of_next_fb_ids if x in fb_ids_requested]
                                already_queued = [x for x in list_of_next_fb_ids if x in fb_ids_to_request]
                                logger.debug(
                                    f"FB walk {set_id=} crawl {len(fb_ids_requested)} {fb_id=} returned {list_of_next_fb_ids=} "
                                    f"{already_requested=} {already_queued=}"
                                )

                                # iterate over list_of_next_fb_ids and add to fb_ids_to_request if not already requested or queued
                                # DM 14th Oct 26 - also check the queue, as an fb_id queued twice was crawled twice
                                for next_fb_id in list_of_next_fb_ids:
                                    if next_fb_id not in fb_ids_requested and next_fb_id not in fb_ids_to_request:
                                        fb_ids_to_request.append(next_fb_id)
                                logger.debug(f"FB walk {set_id=} queue now {fb_ids_to_request=}")
    
                                logger.debug(f"Part 2 - fb_ids_to_request {len(fb_ids_to_request)}")
                                logger.debug(f"Part 2 - fb_ids_requested {len(fb_ids_requested)}")
    
                                if len(fb_ids_to_request) == 0:
                                    logger.debug(f"Part 2 - no more fb_ids to request so end")
                                    break # out of while

                                if len(fb_ids_requested) >= FB_WALK_MAX_CRAWLS:
                                    logger.info(f"Crawled {len(fb_ids_requested)} carousel pages so stopping crawl")
                                    walk_stop_reason = f"crawl cap ({FB_WALK_MAX_CRAWLS})"
                                    break # out of while

                                fb_id = fb_ids_to_request.pop(0)
                                logger.debug(f"Part 2 - total_images {len(to_enrich.media)} - includes duplicates")

                            self._log_fb_walk_summary(
                                url, set_id, fb_ids_requested, fb_ids_to_request, walk_stop_reason,
                                walk_start, to_enrich.media[walk_media_start:],
                            )
                            diag.setdefault("walks", []).append(f"{set_id}:{len(fb_ids_requested)}:{walk_stop_reason}")

                            if len(fb_ids_requested) == 1:
                                logger.debug("Probably the wrong fb_id url here in Part 2, as only 1 fb_ids_requested")
                                logger.debug("so not the carousel we want")
                                logger.debug("continuing to next record in root url (Part 1) ")
                                continue # to next record
    
                            logger.debug(f"Part 2 END of while loop")

                            full_crawl_done = True
                            # return
                        else:
                            # logger.debug('photo string not found in bulk-route-definitions - this is normal. 1 out of 3 have seen work... ')
                            # logger.debug('it also could be a single image which is different')
                            ## TODO - strategy x
                            continue # to next record

        # DM 15th Oct 26 - no "found N relevant media file(s)" line here, as it only counted the screenshot, not the walks'
        # images - the FB summary line has media_added and unique_media
        logger.info(
            f"FB timing - facebook_extract_media_from_wacz took {time.perf_counter() - fb_start:.0f}s for {url} "
            f"({len(unique_set_ids)} set_id(s), {len(to_enrich.media)} media on item incl duplicates)"
        )



    @staticmethod
    def _fb_session_user(warc_filename: str) -> str | None:
        """the facebook user id the crawl's requests were sent as - "0" when logged out, None if there were none"""
        with open(warc_filename, "rb") as warc_stream:
            for record in ArchiveIterator(warc_stream):
                if record.rec_type != "request" or "facebook.com/" not in (record.rec_headers.get_header("WARC-Target-URI") or ""):
                    continue
                match = FB_SESSION_USER.search(record.content_stream().read())
                if match:
                    return match.group(1).decode()
        return None

    @staticmethod
    def _fb_screenshot_problems(to_enrich: Metadata, url: str) -> str:
        """facebook login wall / block phrases OCR finds on the browsertrix screenshot, comma separated, or ok"""
        screenshot = to_enrich.get_media_by_id("browsertrix-screenshot-0")
        try:
            phrases = check_screenshot_for_facebook_issues(screenshot.filename)
        except Exception as e:
            logger.warning(f"FB OCR of browsertrix screenshot failed for {url}: {e}")
            return "ocr_failed"
        if phrases:
            logger.warning(f"Facebook browsertrix screenshot contains warning/login phrases {phrases} for {url}")
            return ",".join(p.replace(" ", "_") for p in phrases)
        return "ok"

    @staticmethod
    def _fb_is_feed_url(url: str) -> bool:
        """True for a profile, page or group url (eg facebook.com/munigaza, profile.php?id=, groups/<id>), whose page is
        a feed of posts, rather than a url for a single post, photo or video"""
        parsed = urlparse(url)
        segments = [s for s in parsed.path.split("/") if s]
        query = parse_qs(parsed.query)
        if not segments:
            return False
        if segments[0] == "profile.php":
            return "story_fbid" not in query and "fbid" not in query
        if segments[0] == "people":
            return True
        if segments[0] == "groups":
            # groups/<id> and groups/<id>/user/<id> are feeds, groups/<id>/posts/<id>, ?multi_permalinks= and
            # ?view=permalink are a post
            is_post = "multi_permalinks" in query or query.get("view") == ["permalink"]
            return (len(segments) <= 2 or segments[2] == "user") and not is_post
        name = segments[0].lower()
        return len(segments) == 1 and name not in FB_NOT_PROFILE_NAMES and not name.endswith(".php")

    @staticmethod
    def _last_page_response(wacz_filename: str, url: str) -> tuple | None:
        """(http headers, body) of the crawler's last response for the page itself, matched ignoring www. and a trailing
        slash. The last one, as eg a Cloudflare challenge which passes is followed by the page. None if there isn't one"""
        host = (urlparse(url).hostname or "").removeprefix("www.")
        path = urlparse(url).path.rstrip("/")
        last = None
        try:
            with ZipFile(wacz_filename) as wacz:
                for name in sorted(n for n in wacz.namelist() if n.startswith("archive/") and n.endswith(".warc.gz")):
                    with wacz.open(name) as warc_stream:
                        for record in ArchiveIterator(warc_stream):
                            if record.rec_type != "response" or not record.http_headers:
                                continue
                            record_url = urlparse(record.rec_headers.get_header("WARC-Target-URI") or "")
                            if (record_url.hostname or "").removeprefix("www.") != host or record_url.path.rstrip("/") != path:
                                continue
                            last = (record.http_headers, record.content_stream().read())
        except Exception as e:
            logger.debug(f"Couldn't read the page's response for {url} from {wacz_filename}: {e}")
            return None
        return last

    @staticmethod
    def _is_cloudflare_challenge(wacz_filename: str, url: str) -> bool:
        """True if the crawler's last response for the page was Cloudflare's challenge (it sends cf-mitigated: challenge)
        rather than the page itself"""
        response = WaczExtractorEnricher._last_page_response(wacz_filename, url)
        return bool(response) and (response[0].get_header("cf-mitigated") or "").lower() == "challenge"

    @staticmethod
    def _youtube_embed_ids(wacz_filename: str, url: str) -> list[str]:
        """ids of the YouTube videos embedded (as iframes) in the crawled page, in page order, once each"""
        response = WaczExtractorEnricher._last_page_response(wacz_filename, url)
        if not response or "html" not in (response[0].get_header("Content-Type") or ""):
            return []
        return list(dict.fromkeys(m.decode() for m in YOUTUBE_EMBED_IFRAME.findall(response[1])))

    def _download_youtube_embeds(self, to_enrich: Metadata, wacz_filename: str, url: str) -> None:
        """downloads the YouTube videos embedded in the crawled page, with the generic extractor. Eg idf.il's bot protection
        gives yt-dlp a block page, so the generic extractor never sees the embeds, yet the crawl gets the page"""
        host = (urlparse(url).hostname or "").lower()
        if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
            return
        video_ids = self._youtube_embed_ids(wacz_filename, url)
        # the generic extractor names a video's file after its id, eg UkI8V8_4OGk.mp4, so skip any it already has
        already = {os.path.basename(m.filename).split(".")[0] for m in to_enrich.media}
        to_download = [v for v in video_ids if v not in already][:MAX_EMBEDDED_YOUTUBE_VIDEOS]
        if not to_download:
            return
        try:
            generic_extractor = self.module_factory.get_module("generic_extractor", self.config)
        except Exception as e:
            logger.warning(f"Couldn't load the generic extractor to download the page's YouTube embeds {to_download}: {e}")
            return
        downloaded = 0
        for video_id in to_download:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            try:
                video = generic_extractor.download(Metadata().set_url(video_url))
            except Exception as e:
                logger.warning(f"Failed to download YouTube video {video_url} embedded in {url}: {e}")
                continue
            if not video:
                logger.warning(f"No media found for YouTube video {video_url} embedded in {url}")
                continue
            downloaded += 1
            # the video and its thumbnail
            for media in video.media:
                to_enrich.add_media(media)
        logger.info(
            f"Downloaded {downloaded} of {len(to_download)} YouTube video(s) embedded in the page "
            f"({len(video_ids)} found, at most {MAX_EMBEDDED_YOUTUBE_VIDEOS})"
        )

    @staticmethod
    def _is_bbc_url(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == d or host.endswith(f".{d}") for d in ("bbc.co.uk", "bbc.com"))

    @staticmethod
    def _bbc_image_key(url: str) -> str | None:
        """id of a BBC image, the same at every width, eg the uuid in .../cpsprodpb/db61/live/<uuid>.jpg.webp.
        None if the url isn't a BBC image (eg site graphics on static.files.bbci.co.uk)"""
        m = BBC_IMAGE_KEY.search(url)
        return (m.group(1) or m.group(2)) if m else None

    @staticmethod
    def _is_aljazeera_url(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == d or host.endswith(f".{d}") for d in ("aljazeera.com", "aljazeera.net"))

    @staticmethod
    def _aljazeera_image_key(url: str) -> str | None:
        """path of an Al Jazeera image, the same at every size. None if the url isn't one, eg a Brightcove video
        thumbnail on boltdns.net (the generic extractor already has the page's videos' thumbnails)"""
        if not WaczExtractorEnricher._is_aljazeera_url(url):
            return None
        m = AJ_IMAGE_PATH.match(urlparse(url).path)
        return m.group(0) if m else None

    @staticmethod
    def _aljazeera_page_image_keys(warc_filename: str, url: str) -> set | None:
        """paths of the images of the page's own post (its featured image and any in its body), from the post in the
        page's apollo state, not the other stories (most read, more on, latest videos) the page also shows.
        None if the page's html or post isn't in the warc, so every image is kept as before"""
        path = urlparse(url).path.rstrip("/")
        with open(warc_filename, "rb") as warc_stream:
            for record in ArchiveIterator(warc_stream):
                if record.rec_type != "response":
                    continue
                record_url = record.rec_headers.get_header("WARC-Target-URI") or ""
                if urlparse(record_url).path.rstrip("/") != path or not WaczExtractorEnricher._is_aljazeera_url(record_url):
                    continue
                if "text/html" not in (record.http_headers.get_header("Content-Type") or ""):
                    continue
                m = AJ_APOLLO_STATE.search(record.content_stream().read().decode("utf-8", errors="replace"))
                try:
                    state = json.loads(base64.b64decode(m.group(1))) if m else {}
                except ValueError:
                    state = {}
                posts = [
                    v for v in state.values() if isinstance(v, dict) and (v.get("link") or "").rstrip("/") == path
                ]
                if not posts:
                    logger.debug("Al Jazeera page's post not found in its apollo state, keeping all images")
                    return None
                return {key for post in posts for key in AJ_IMAGE_PATH.findall(json.dumps(post))}
        logger.debug(f"Al Jazeera page html for {url} not found in the WACZ, keeping all images")
        return None

    @staticmethod
    def _youtube_video_id(url: str) -> str | None:
        """id of the video a YouTube page url is for, None for other pages (eg a channel) or other sites"""
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host == "youtu.be":
            video_id = parsed.path.strip("/")
            return video_id if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) else None
        if host != "youtube.com" and not host.endswith(".youtube.com"):
            return None
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            return video_id if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) else None
        m = YOUTUBE_PATH_VIDEO_ID.match(parsed.path)
        return m.group(1) if m else None

    @staticmethod
    def _ytimg_video_id(url: str) -> str | None:
        """id of the video a YouTube thumbnail url is for, None if the url isn't a video thumbnail"""
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host != "ytimg.com" and not host.endswith(".ytimg.com"):
            return None
        m = YTIMG_VIDEO_ID.match(parsed.path)
        return m.group(1) if m else None

    @staticmethod
    def _bbc_article_image_keys(warc_filename: str, url: str) -> set | None:
        """ids of the images in a BBC page's <article> (the story itself, not top stories/most read etc).
        Matched on path as bbc.com may have redirected to bbc.co.uk.
        None if the page's html isn't in the warc or has no single <article>, so every image is kept as before"""
        path = urlparse(url).path.rstrip("/")
        with open(warc_filename, "rb") as warc_stream:
            for record in ArchiveIterator(warc_stream):
                if record.rec_type != "response":
                    continue
                record_url = record.rec_headers.get_header("WARC-Target-URI") or ""
                if urlparse(record_url).path.rstrip("/") != path or not WaczExtractorEnricher._is_bbc_url(record_url):
                    continue
                if "text/html" not in (record.http_headers.get_header("Content-Type") or ""):
                    continue
                articles = BeautifulSoup(record.content_stream().read(), "html.parser").find_all("article")
                if len(articles) != 1:
                    logger.debug(f"BBC page has {len(articles)} <article> elements, keeping all images")
                    return None
                return {m.group(1) or m.group(2) for m in BBC_IMAGE_KEY.finditer(str(articles[0]))}
        logger.debug(f"BBC page html for {url} not found in the WACZ, keeping all images")
        return None

    @staticmethod
    def _fb_own_post_id(warc_filename: str, url: str) -> str | None:
        """post_id of the post the url is for - the first story in the page's html which isn't a recommended post.
        None if the page's html isn't in the warc or has no stories"""
        with open(warc_filename, "rb") as warc_stream:
            for record in ArchiveIterator(warc_stream):
                if record.rec_type != "response" or record.rec_headers.get_header("WARC-Target-URI") != url:
                    continue
                if "text/html" not in (record.http_headers.get_header("Content-Type") or ""):
                    continue
                body = record.content_stream().read()
                recommended = {m.group(1) for m in FB_RECOMMENDED_POST_ID.finditer(body)}
                for m in FB_POST_ID.finditer(body):
                    if m.group(1) not in recommended:
                        return m.group(1).decode()
                return None
        return None

    @staticmethod
    def _fb_post_has_photo(warc_filename: str, url: str, fb_id: str) -> bool:
        """True if the post page's html has the photo as a story's attachment, eg
        "attachments":[{"media":{"__typename":"Photo","__isNode":"Photo","id":"<fb_id>" - not just any node with its id"""
        attachment = b'"media":{"__typename":"Photo","__isNode":"Photo","id":"' + fb_id.encode() + b'"'
        with open(warc_filename, "rb") as warc_stream:
            for record in ArchiveIterator(warc_stream):
                if record.rec_type != "response" or record.rec_headers.get_header("WARC-Target-URI") != url:
                    continue
                if "text/html" not in (record.http_headers.get_header("Content-Type") or ""):
                    continue
                return attachment in record.content_stream().read()
        return False

    def _facebook_extract_screenshots(self, to_enrich: Metadata, warc_filename: str) -> int:
        """The browsertrix screenshot(s) from a Facebook page's wacz. Returns the number added"""
        counter_screenshots = 0
        with open(warc_filename, "rb") as warc_stream:
            for record in ArchiveIterator(warc_stream):
                if record.rec_type == "resource" and record.content_type == "image/png":
                    # DM 15th Oct 26 - not warc-file-<n>.png, as Strategy 0 names a png image that and overwrote the screenshot
                    fn = os.path.join(self.tmp_dir, f"browsertrix-screenshot-{counter_screenshots}.png")
                    with open(fn, "wb") as outf:
                        outf.write(record.raw_stream.read())
                    to_enrich.add_media(Media(filename=fn), f"browsertrix-screenshot-{counter_screenshots}")
                    logger.debug(f"Part 1 - Added Screenshot")
                    counter_screenshots += 1
        return counter_screenshots

    @staticmethod
    def _fb_photo_image_path(warc_filename: str, url: str) -> str | None:
        """url path of the full res image of the photo a /photo?fbid= page is for, from the photo's node in the page's
        html. None if there is no fbid, the page's html isn't in the warc or the node has no image"""
        fbid = parse_qs(urlparse(url).query).get("fbid", [None])[0]
        if not fbid or not fbid.isdigit():
            return None
        with open(warc_filename, "rb") as warc_stream:
            for record in ArchiveIterator(warc_stream):
                if record.rec_type != "response" or record.rec_headers.get_header("WARC-Target-URI") != url:
                    continue
                if "text/html" not in (record.http_headers.get_header("Content-Type") or ""):
                    continue
                body = record.content_stream().read()
                for node in re.finditer(rb'"id":"' + fbid.encode() + rb'"', body):
                    image = FB_PHOTO_IMAGE_URI.search(body, node.end(), node.end() + FB_PHOTO_IMAGE_WINDOW)
                    if image:
                        return urlparse(image.group(1).decode().replace("\\/", "/")).path
                return None
        return None

    def _facebook_extract_single_photo(self, to_enrich: Metadata, warc_filename: str, photo_path: str | None = None) -> int:
        """Part 1 - Strategy 0 - the full resolution image from a /photo page's wacz. Returns the number of media added.
        With photo_path only that image is kept (once, whatever its size), otherwise every image passing the size filters"""
        tmp_dir = self.tmp_dir
        counter_warc_files = 0
        seen_urls = set()

        with open(warc_filename, "rb") as warc_stream:
            for record in ArchiveIterator(warc_stream):
                if record.rec_type == "response": pass
                else: continue

                # so response has this header!
                record_url = record.rec_headers.get_header("WARC-Target-URI")
                if UrlUtil.is_relevant_url(record_url): pass
                else: continue

                if record_url in seen_urls:
                    logger.debug(f"Skipping already seen URL {record_url}.")
                    continue

                if photo_path and (urlparse(record_url).path != photo_path or counter_warc_files): continue

                # filter by media mimetypes
                content_type = record.http_headers.get("Content-Type")
                if content_type: pass
                else: continue

                if any(x in content_type for x in ["video", "image", "audio"]): pass
                else: continue

                # create local file and add media
                ext = mimetypes.guess_extension(content_type)

                warc_fn = f"warc-file-{counter_warc_files}{ext}"

                fn = os.path.join(tmp_dir, warc_fn)

                with open(fn, "wb") as outf:
                    outf.write(record.raw_stream.read())

                fs = os.path.getsize(fn)
                if ext == None : continue
                if not photo_path:
                    if fs < 13500 and ext == ".jpg": continue
                    if fs < 6000 and ext == ".webp": continue
                    if fs < 37000 and ext == ".png": continue
                    # there are files from youtube avif < 24kb which are thumbnails previews images.
                    # todo dm 16th Sept 2026 - possibly do <24k to get rid of these
                    if fs < 12000 and ext == ".avif": continue
                    if ext == ".gif": continue
                    if ext == ".ico": continue

                logger.debug(f"Part 1 - Strategy 0 - saving the single relevant full resolution image from wacz")
                logger.debug(f"Part 1 - Added {fn} which is {fs} and extension {ext}")
                m = Media(filename=fn)
                m.set("src", record_url)

                to_enrich.add_media(m, warc_fn)
                counter_warc_files += 1
                seen_urls.add(record_url)

        return counter_warc_files

    def _log_fb_walk_summary(self, url, set_id, fb_ids_requested, fb_ids_to_request, stop_reason, walk_start, walk_media) -> None:
        """DM 8th Oct 26 - one line per Part 2 carousel walk, to diagnose ~20 min FB posts (crawl count, time, repeated images)"""
        hashes = Counter(self._short_file_hash(m.filename) for m in walk_media)
        repeated = {h: n for h, n in hashes.items() if n > 1}
        logger.info(
            f"FB walk summary {url} {set_id=} stopped: {stop_reason} - {len(fb_ids_requested)} crawl(s) in "
            f"{time.perf_counter() - walk_start:.0f}s, {len(walk_media)} media added, {len(hashes)} unique by content, "
            f"{len(fb_ids_to_request)} fb_id(s) left in queue"
        )
        logger.debug(f"FB walk summary {set_id=} {fb_ids_requested=} repeated content hash:count {repeated}")

    @staticmethod
    def _first_fb_photo_route(foo: str) -> tuple[str, str | None, bool] | None:
        """fb_id, raw set value (None if the route has no set) and whether another param follows the set, for the
        first photo route in a bulk-route-definitions body - only the first route has ever been used"""
        pos = foo.find("photo%2F%3Ffbid%3D")
        match = FB_PHOTO_ROUTE.match(foo, pos) if pos > 0 else None
        if not match:
            return None
        fb_id, set_value, more_params = match.groups()
        return fb_id, set_value, more_params is not None

    @staticmethod
    def _fb_carousel_set_id(set_value: str | None, more_params: bool) -> str | None:
        """set_id of a post carousel (pcb.<id>) followed by another param - the only sets the old +13 slice parsed
        correctly. A pcb set ending the route came out non-numeric and was skipped, so is still skipped here"""
        match = FB_CAROUSEL_SET.fullmatch(set_value or "")
        return match.group(1) if match and more_params else None

    @staticmethod
    def _short_file_hash(filename) -> str:
        try:
            with open(filename, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()[:10]
        except (OSError, TypeError):
            return "unreadable"

    def _facebook_extract_album_photo(self, to_enrich: Metadata, fb_id: str, set_value: str) -> int:
        """DM 23rd Oct 26 - a single photo post has no carousel, and its photo's route is in the author's album
        (set=a.<id>). Crawl that photo's /photo page once, not walking the album, for its full res image as Strategy 0
        does. The post page only has a smaller copy (eg 526x1140 of a 1080x2340 photo). Returns the number of media added"""
        photo_url = f"https://www.facebook.com/photo/?fbid={fb_id}&set={set_value}"
        logger.info(f"FB single photo post, crawling its photo page {photo_url}")
        crawl = self._fb_browsertrix_crawl(photo_url, fb_id)
        if crawl is None:
            return 0
        _, warc_filename, _ = crawl
        # only the photo's own image - every image passing the size filters would include other photos in the album
        photo_path = self._fb_photo_image_path(warc_filename, photo_url)
        if not photo_path:
            logger.info(f"FB photo's node not found in the page's html for {photo_url}, so no image added")
            return 0
        counter_warc_files = self._facebook_extract_single_photo(to_enrich, warc_filename, photo_path)
        if counter_warc_files == 0:
            logger.info(f"FB photo image {photo_path=} not in the wacz, so no image added")
        return counter_warc_files

    # only used by FB - Part 2
    def save_images_to_enrich_object_from_url_using_browsertrix(self, url_build, to_enrich: Metadata, current_fb_id, set_id) -> list:
            # returns the carousel's next fb_ids - empty if the crawl failed, as the caller iterates over it
            logger.debug(f' Inside Part 2')
            crawl = self._fb_browsertrix_crawl(url_build, current_fb_id)
            if crawl is None:
                return []  # no next fb_ids, so Part 2 carries on with any it already has
            collection, warc_filename, crawl_seconds = crawl

            linux_tmp_dir = os.path.expanduser('~/aatmp')
            counter = 100
            seen_urls = set()
            # next_fb_id = 0
            list_of_next_fb_ids = []
            added_this_crawl = []  # size:hash:url-path-tail, to spot the same UI image added on every crawl
            with open(warc_filename, 'rb') as warc_stream:
                for record in ArchiveIterator(warc_stream):

                    # 1.Get next fb_id logic
                    if record.rec_type == 'request': 
                        uri = record.rec_headers.get_header('WARC-Target-URI')
                        if "bulk-route-definitions/" in uri:
                            content = record.content_stream().read()
                            foo = str(content)

                            # Strategy 1 test
                            # photo%2F%3Ffbid%3D1646726009098072%26set%3Dpcb.1646726145764725%26
                            # fbid = 1646726009098072
                            # set = pcb.1646726145764725

                            route = self._first_fb_photo_route(foo)

                            if route:
                                next_fb_id_foo, set_value, _ = route

                                # DM 14th Oct 26 - don't follow a photo explicitly in another set (eg the author's album).
                                # A route with no set, or with pcb.<set_id> ending the route, is still followed as before
                                if set_value is not None and set_value != f"pcb.{set_id}":
                                    logger.debug(f"Part 2 - ignoring fb_id {next_fb_id_foo} in {set_value=}, not this carousel pcb.{set_id}")
                                # check haven't found current page fb_id
                                elif next_fb_id_foo == current_fb_id:
                                    logger.debug("found current fb_id so ignoring")
                                else:
                                    # next_fb_id = next_fb_id_foo
                                    list_of_next_fb_ids.append(next_fb_id_foo)
                                    # logger.debug(f'Part 2 - appending fb_id {next_fb_id}')

                            # else:
                                # logger.debug('photo string not found in bulk-route-definitions - this is normal. 1 out of 3 have seen work')
                                

                        continue # to next record

                    # DM 12th June 25
                    # am getting weird links coming here which are not part of a carousel
                    # so if is this true, then there is no next_fb_id, so go to next record
                    if len(list_of_next_fb_ids) == 0: 
                        # logger.debug(f"Part 2 - probably a spurious record as couldn't find a next fb_id so not the carousel we want")
                        continue

                    # the response record should have a full res image   
                    if record.rec_type == 'response': pass
                    else: continue # to next record

                    record_url = record.rec_headers.get_header('WARC-Target-URI')
                    # DM 15th Oct 26 - eg facebook.com/rsrc.php/ interface images added on every crawl
                    if not UrlUtil.is_relevant_url(record_url): continue

                    content_type = record.http_headers.get("Content-Type")
                    if not content_type: continue
                    if not any(x in content_type for x in ["video", "image", "audio"]): continue

                    # DM - ignore this specialised content type for facebook
                    if content_type == "image/x.fb.keyframes": continue

                    # create local file with correct extension
                    ext = mimetypes.guess_extension(content_type)
                    if ext == None and content_type == "image/x-icon":
                        logger.debug(f"Part 2 - content_type {content_type} has not guessed correct extension. Ignoring.")
                        continue

                    if ext == None:
                        logger.warning(f"Part 2 - content_type {content_type}. Not seen before. Investigate.")
                        continue
                    
                    warc_fn = f"warc-file-{collection}-{counter}{ext}"
                    fn = os.path.join(linux_tmp_dir, warc_fn)

                    with open(fn, "wb") as outf: outf.write(record.raw_stream.read())

                    fs = os.path.getsize(fn)

                    should_add_media = True
                    if fs < 13500 and ext == ".jpg": should_add_media = False
                    if fs < 6000 and ext == ".webp": should_add_media = False
                    if fs < 37000 and ext == ".png": should_add_media = False
                    if fs < 12000 and ext == ".avif": should_add_media = False
                    if ext == ".gif": should_add_media = False
                    if ext == ".ico": should_add_media = False
                    if ext == None : should_add_media = False

                    # remove bad videos eg tiny DASH init/segment fragments which ffmpeg can't read
                    m = Media(filename=fn)
                    if should_add_media and m.is_video() and not m.is_valid_video(): should_add_media = False

                    # add media
                    # there will be duplicates but deduplication is handled in the file upload code 
                    if should_add_media:
                        m.set("src", record_url)
                        m.set("src_alternative", record_url)
                        to_enrich.add_media(m, warc_fn)
                        logger.debug(f"Part 2 - Added {fn} which is {fs} bytes and extension {ext}")
                        added_this_crawl.append(f"{fs}:{self._short_file_hash(fn)}:{urlparse(record_url).path[-40:]}")
                        counter += 1 # starts at 100 and makes filename unique  
                        seen_urls.add(record_url)
                    else:
                        # delete from the tmp_dir so I can see what is there for debugging
                        os.remove(fn)
                    
                    # logger.debug('Part 2 - On to next record')

            logger.info(
                f"FB crawl {current_fb_id=} took {crawl_seconds:.0f}s, added {len(added_this_crawl)} media, "
                f"next fb_ids {list_of_next_fb_ids}"
            )
            logger.debug(f"FB crawl {current_fb_id=} added size:hash:path {added_this_crawl}")
            # is normally a list of 2 id's for a carousel
            return list_of_next_fb_ids

    def _fb_browsertrix_crawl(self, url_build: str, current_fb_id: str) -> tuple[str, str, float] | None:
            """crawl one FB page into ~/aatmp with the logged in profile. Returns the collection, warc filename and
            crawl seconds, or None if the crawl failed"""
            # call browsertrix and get a warc file using a logged in facebook profile
            # this will get full resolution image which we can then save as a jpg

            with open('url.txt', 'w') as file:
                file.write(url_build)

            collection = random_str(8)

            linux_tmp_dir = os.path.expanduser('~/aatmp')

            browsertrix_home = linux_tmp_dir

            docker_commands = ["docker", "run", "--rm", "-v", f"{browsertrix_home}:/crawls/", "webrecorder/browsertrix-crawler"]
            cmd = docker_commands + [
                "crawl",
                "--url", url_build,
                "--scopeType", "page",
                "--generateWACZ",
                "--text",
                "--screenshot", "fullPage",
                "--collection", collection,
                "--behaviors", "autoscroll,autoplay,autofetch,siteSpecific",
                "--behaviorTimeout", str(self.timeout),
                "--timeout", str(self.timeout),
                "--combineWarc"
            ]

            if self.profile:
                profile_fn = os.path.join(browsertrix_home, "profile.tar.gz")
                logger.debug(f"copying {self.profile} to {profile_fn}")
                shutil.copyfile(self.profile, profile_fn)
                cmd.extend(["--profile", os.path.join("/crawls", "profile.tar.gz")])

            crawl_start = time.perf_counter()
            try:
                logger.info(f"Running browsertrix-crawler: {' '.join(cmd)}")
                subprocess.run(cmd, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as e:
                logger.error(f"WACZ generation failed: {e}")
                if crawler_log := self._summarise_crawler_output(e.stdout, e.stderr):
                    logger.error(f"browsertrix-crawler output:\n{crawler_log}")
                logger.debug(f"FB crawl {current_fb_id=} failed after {time.perf_counter() - crawl_start:.0f}s")
                return None
            except Exception as e:
                logger.error(f"WACZ generation failed: {e}")
                return None
            crawl_seconds = time.perf_counter() - crawl_start

            if os.getenv('RUNNING_IN_DOCKER'):
                filename = os.path.join("collections", collection, f"{collection}.wacz")
            else:
                filename = os.path.join(browsertrix_home, "collections", collection, f"{collection}_0.warc.gz")

            if not os.path.exists(filename):
                logger.warning(f"Unable to locate and upload WACZ  {filename=}")
                return None

            return collection, filename, crawl_seconds


    # Extract media for non FB link
    def extract_media_from_wacz(self, to_enrich: Metadata, wacz_filename: str) -> None:
        """
        Receives a .wacz archive, and extracts all relevant media from it, adding them to to_enrich.
        """
        logger.info(f"WACZ extract_media or extract_screenshot flag is set, extracting media from {wacz_filename=}")

        # unzipping the .wacz
        tmp_dir = self.tmp_dir
        unzipped_dir = os.path.join(tmp_dir, "unzipped")
        with ZipFile(wacz_filename, "r") as z_obj:
            z_obj.extractall(path=unzipped_dir)

        # if warc is split into multiple gzip chunks, merge those
        warc_dir = os.path.join(unzipped_dir, "archive")
        warc_filename = os.path.join(tmp_dir, "merged.warc")
        with open(warc_filename, "wb") as outfile:
            for filename in sorted(os.listdir(warc_dir)):
                if filename.endswith(".gz"):
                    chunk_file = os.path.join(warc_dir, filename)
                    with open(chunk_file, "rb") as infile:
                        shutil.copyfileobj(infile, outfile)

        # get media out of .warc
        counter_warc_files = 0
        counter_screenshots = 0
        seen_urls = set()

        # DM 21st Oct 26 - a BBC article page has ~40 thumbnails of other stories (top stories, most read) each at
        # many widths, so only keep the images in the page's <article>, once each at the largest size
        # DM 22nd Oct 26 - likewise Al Jazeera, keeping its post's own images. It also drops the Brightcove thumbnails
        # of the page's videos, which the generic extractor already has
        url = to_enrich.get_url()
        page_image_keys, image_key_for, site = None, None, None
        if url and self._is_bbc_url(url):
            page_image_keys, image_key_for, site = self._bbc_article_image_keys(warc_filename, url), self._bbc_image_key, "BBC article"
        elif url and self._is_aljazeera_url(url):
            page_image_keys, image_key_for, site = self._aljazeera_page_image_keys(warc_filename, url), self._aljazeera_image_key, "Al Jazeera page"
        page_media = {}
        page_skipped = 0

        # DM 22nd Oct 26 - a YouTube video page also loads the thumbnails of the recommended videos in its sidebar,
        # so only keep the thumbnails of the video being archived
        youtube_video_id = self._youtube_video_id(url) if url else None
        youtube_skipped = 0

        # DM 10th Nov 25 - turn off vpn if active to see if fixes issue with download_from_url
        # that gets best images from twitter
        # only attempt if an x.com link - seems hard so just take the 3s hit for now
        # RUNNING_IN_WSL is an env variable I set in launch.json settings

        # DM 17th Sep 26 - skip entirely if VPN isn't in use, no point checking/disconnecting
        # something that was never connected
        if self.config.get("use_vpn", False):
            is_wsl = os.getenv('RUNNING_IN_WSL', 'false').lower() == 'true'
            if not is_wsl:
                logger.debug("Attempting to disconnect VPN")
                subprocess.run(['expressvpnctl', 'disconnect'], capture_output=True)
                max_retries = 10
                for i in range(max_retries):
                    logger.debug(f"Checking VPN disconnection status, attempt {i+1}/{max_retries}")
                    status = subprocess.run(['expressvpnctl', 'status'], capture_output=True, text=True)
                    if 'Disconnected' in status.stdout:
                        logger.info("VPN disconnected and sleeping for a few seconds")
                        time.sleep(3)  # Give VPN routing tables time to stabilise
                        break
                    time.sleep(1)
            else:
                logger.info("Running in WSL2, skipping VPN disconnect")


        with open(warc_filename, "rb") as warc_stream:
            for record in ArchiveIterator(warc_stream):
                # only include fetched resources
                if (
                    record.rec_type == "resource" and record.content_type == "image/png" and self.extract_screenshot
                ):  # screenshots
                    fn = os.path.join(tmp_dir, f"browsertrix-screenshot-{counter_screenshots}.png")
                    with open(fn, "wb") as outf:
                        outf.write(record.raw_stream.read())
                    m = Media(filename=fn)
                    logger.info(f"Adding screenshot from WACZ")
                    to_enrich.add_media(m, f"browsertrix-screenshot-{counter_screenshots}")
                    counter_screenshots += 1
                if not self.extract_media:
                    continue

                if record.rec_type != "response":
                    continue
                record_url = record.rec_headers.get_header("WARC-Target-URI")
                if not UrlUtil.is_relevant_url(record_url):
                    # logger.debug(f"Skipping irrelevant URL {record_url} but it's still present in the WACZ.")
                    continue
                if record_url in seen_urls:
                    logger.debug(f"Skipping already seen URL {record_url}.")
                    continue

                # filter by media mimetypes
                content_type = record.http_headers.get("Content-Type")
                if not content_type:
                    continue
                if not any(x in content_type for x in ["video", "image", "audio"]):
                    continue

                # create local file and add media
                ext = mimetypes.guess_extension(content_type)
                warc_fn = f"warc-file-{counter_warc_files}{ext}"
                fn = os.path.join(tmp_dir, warc_fn)

                record_url_best_qual = UrlUtil.twitter_best_quality_url(record_url)
                with open(fn, "wb") as outf:
                    outf.write(record.raw_stream.read())

                # DM 28th May 25 - special cases Instagram
                if 'instagram' in record_url:
                    # https://static.cdninstagram.com/images/instagram/xig_legacy_spritesheets/sprite_core.png?__makehaste_cache_breaker=VftLCxPPZoi
                    # https://www.instagram.com/images/instagram/xig_legacy_spritesheets/sprite_compassion_2x.png?__makehaste_cache_breaker=qw4qS1SUQa2
                    if '__makehaste_cache_breaker' in record_url:
                        continue
                    # phone image and icons
                    if 'https://www.instagram.com/rsrc.php/v4/' in record_url:
                        continue

                # DM 22nd May 25 - only want larger more important images 
                fs = os.path.getsize(fn)
                if fs < 13500 and ext == ".jpg": continue
                if fs < 6000 and ext == ".webp": continue
                # png's I see them mostly as graphics or artwork.
                # screenshots are pngs from browsertrix
                if fs < 37000 and ext == ".png": continue
                # youtube
                if fs < 12000 and ext == ".avif": continue
                if ext == ".gif": continue
                if ext == ".ico": continue
                if ext == None : continue

                if youtube_video_id:
                    thumbnail_video_id = self._ytimg_video_id(record_url)
                    if thumbnail_video_id and thumbnail_video_id != youtube_video_id:
                        youtube_skipped += 1
                        continue

                image_key = None
                if page_image_keys is not None and "image" in content_type:
                    image_key = image_key_for(record_url)
                    if image_key not in page_image_keys:
                        page_skipped += 1
                        continue
                    if image_key in page_media:
                        # another width of an image already added - keep whichever is larger
                        kept = page_media[image_key]
                        if fs > os.path.getsize(kept.filename):
                            os.replace(fn, kept.filename)
                            kept.set("src", record_url)
                        seen_urls.add(record_url)
                        continue

                logger.debug(f"Adding media from WACZ: {fn} which is {fs} bytes and extension {ext}")

                m = Media(filename=fn)
                m.set("src", record_url)
                # if a link with better quality exists, try to download that
                if record_url_best_qual != record_url:
                    try:
                        best_quality_filename = self.download_from_url(record_url_best_qual, warc_fn, quiet_on_failure=True)
                        if best_quality_filename:
                            m.filename = best_quality_filename
                            m.set("src", record_url_best_qual)
                            m.set("src_alternative", record_url)
                        else:
                            logger.info(
                                f"Best quality URL {record_url_best_qual=} could not be downloaded (e.g. 404), using original from WARC."
                            )
                    except Exception as e:
                        logger.info(
                            f"Unable to download best quality URL for {record_url=} got error {e}, using original in WARC."
                        )

                # remove bad videos
                if m.is_video() and not m.is_valid_video():
                    continue

                # DM 10th Nov 25 - catch weird vpn with wacz
                try:
                    # filesize2 = m.get_filesize()
                    logger.info(f"Adding media from WACZ: {m}" )
                    to_enrich.add_media(m, warc_fn)
                    if image_key:
                        page_media[image_key] = m
                except Exception as e:
                    logger.warning(f"Unable to add media got error {e}")
                counter_warc_files += 1
                seen_urls.add(record_url)
        if page_image_keys is not None:
            logger.info(
                f"{site}: kept {len(page_media)} of {len(page_image_keys)} image(s) of its own, "
                f"skipped {page_skipped} image(s) from the rest of the page"
            )
        if youtube_skipped:
            logger.info(f"YouTube video {youtube_video_id}: skipped {youtube_skipped} thumbnail(s) of other videos")
        logger.info(
            f"WACZ extract_media/extract_screenshot finished, found {counter_warc_files + counter_screenshots} relevant media file(s)"
        )
