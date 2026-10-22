import json
import re

from dateutil.parser import parse as parse_dt
from yt_dlp.extractor.brightcove import BrightcoveNewIE
from yt_dlp.extractor.common import InfoExtractor

from auto_archiver.core.extractor import Extractor
from auto_archiver.core.metadata import Metadata
from auto_archiver.utils import traverse_obj
from auto_archiver.utils.custom_logger import logger

from .dropin import GenericDropin

# DM 22nd Oct 26 - an article's videos are Brightcove players in its body html, eg
#   <video-js id="6377863811112" data-video-id="6377863811112" data-account="665003303001" data-player="6tKQRAx7lu"
#             ... data-embed="default" ...>
# yt-dlp's AlJazeeraIE only gets the article's featured video, and fails when there isn't one (the api returns an
# empty id, so it builds ...index.html?videoId=)
VIDEO_JS_TAG = re.compile(r"<video-js\b[^>]*>")

# as in yt-dlp's AlJazeeraIE, plus video pages of a show, eg /video/newsfeed/2025/5/19/<slug>, which are episodes
# with the video as their featured video
ARTICLE_URL = re.compile(
    r"https?://(?P<base>\w+\.aljazeera\.\w+)/(?P<type>(?:programs?|video)/[^/]+|(?:feature|video|new)s)?"
    r"/\d{4}/\d{1,2}/\d{1,2}/(?P<id>[^/?&#]+)"
)
WP_SITES = {"balkans.aljazeera.net": "ajb", "chinese.aljazeera.net": "chinese", "mubasher.aljazeera.net": "ajm"}
POST_TYPES = {
    "features": "post",
    "program": "episode",
    "programs": "episode",
    "video": "episode",
    "videos": "video",
    "news": "news",
}
DEFAULT_ACCOUNT = "911432371001"
DEFAULT_PLAYER = "csvTfAlKW"


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf'\b{name}="([^"]*)"', tag)
    return m.group(1) if m else None


def brightcove_urls(article: dict) -> list[str]:
    """player urls of the article's featured video and the videos in its body, in page order, once each"""
    videos = []
    featured = article.get("video") or {}
    if featured.get("id"):
        videos.append(
            (
                featured.get("accountId") or DEFAULT_ACCOUNT,
                featured.get("playerId") or DEFAULT_PLAYER,
                "default",
                featured["id"],
            )
        )
    for tag in VIDEO_JS_TAG.findall(article.get("content") or ""):
        video_id = _attr(tag, "data-video-id")
        if video_id:
            videos.append(
                (
                    _attr(tag, "data-account") or DEFAULT_ACCOUNT,
                    _attr(tag, "data-player") or DEFAULT_PLAYER,
                    _attr(tag, "data-embed") or "default",
                    video_id,
                )
            )
    urls = [f"https://players.brightcove.net/{a}/{p}_{e}/index.html?videoId={v}" for a, p, e, v in videos]
    return list(dict.fromkeys(urls))


class Aljazeera(GenericDropin):
    def suitable(self, url, info_extractor: InfoExtractor) -> bool:
        return bool(ARTICLE_URL.match(url))

    def skip_ytdlp_download(self, url, ie_instance: InfoExtractor) -> bool:
        return True

    def extract_post(self, url: str, ie_instance: InfoExtractor) -> dict:
        # the same graphql request AlJazeeraIE makes, keeping the article's body
        base, post_type, display_id = ARTICLE_URL.match(url).groups()
        wp = WP_SITES.get(base) or "aje"
        data = ie_instance._download_json(
            f"https://{base}/graphql",
            display_id,
            query={
                "wp-site": wp,
                "operationName": "ArchipelagoSingleArticleQuery",
                "variables": json.dumps(
                    {"name": display_id, "postType": POST_TYPES[(post_type or "news").split("/")[0]]}
                ),
            },
            headers={"wp-site": wp},
        )
        article = traverse_obj(data, ("data", "article")) or {}
        return {
            "title": article.get("title"),
            "description": article.get("excerpt"),
            "date": article.get("date"),
            "author": traverse_obj(article, ("author", ..., "name")) or None,
            "video_urls": brightcove_urls(article),
        }

    def create_metadata(self, post: dict, ie_instance: InfoExtractor, archiver: Extractor, url: str) -> Metadata:
        result = Metadata()
        result.set_url(url)
        if post.get("date"):
            result.set_timestamp(parse_dt(post["date"]))

        ydl = ie_instance._downloader
        downloaded = 0
        for video_url in post["video_urls"]:
            try:
                video = archiver.get_metadata_for_video({}, BrightcoveNewIE, video_url, ydl)
            except Exception as e:
                logger.warning(f"Failed to download Al Jazeera video {video_url}: {e}")
                continue
            if not video:
                logger.warning(f"No media found for Al Jazeera video {video_url}")
                continue
            downloaded += 1
            # the video and its thumbnail
            for media in video.media:
                result.add_media(media)

        logger.info(f"Al Jazeera article: downloaded {downloaded} of {len(post['video_urls'])} video(s)")
        return result
