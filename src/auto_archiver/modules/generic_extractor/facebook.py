import json
import re
import urllib.parse
from .dropin import GenericDropin
from auto_archiver.core.metadata import Metadata
from auto_archiver.core.media import Media
from auto_archiver.utils.custom_logger import logger
from auto_archiver.utils.misc import ydl_entry_to_filename
from yt_dlp.compat import compat_etree_fromstring
from yt_dlp.extractor.facebook import FacebookIE

# TODO: Remove if / when  https://github.com/yt-dlp/yt-dlp/pull/12275 is merged
from yt_dlp.utils import (
    clean_html,
    get_element_by_id,
    traverse_obj,
    get_first,
    merge_dicts,
    int_or_none,
    parse_count,
    MaxDownloadsReached,
    float_or_none,
    url_or_none,
)


def _extract_metadata(self, webpage, video_id):
    post_data = [
        self._parse_json(j, video_id, fatal=False)
        for j in re.findall(r"data-sjs>({.*?ScheduledServerJS.*?})</script>", webpage)
    ]
    post = (
        traverse_obj(
            post_data,
            (..., "require", ..., ..., ..., "__bbox", "require", ..., ..., ..., "__bbox", "result", "data"),
            expected_type=dict,
        )
        or []
    )
    media = traverse_obj(
        post,
        (
            ...,
            "attachments",
            ...,
            lambda k, v: (k == "media" and str(v["id"]) == video_id and v["__typename"] == "Video"),
        ),
        expected_type=dict,
    )
    title = get_first(media, ("title", "text"))
    description = get_first(media, ("creation_story", "comet_sections", "message", "story", "message", "text"))
    page_title = title or self._html_search_regex(
        (
            r'<h2\s+[^>]*class="uiHeaderTitle"[^>]*>(?P<content>[^<]*)</h2>',
            r'(?s)<span class="fbPhotosPhotoCaption".*?id="fbPhotoPageCaption"><span class="hasCaption">(?P<content>.*?)</span>',
            self._meta_regex("og:title"),
            self._meta_regex("twitter:title"),
            r"<title>(?P<content>.+?)</title>",
        ),
        webpage,
        "title",
        default=None,
        group="content",
    )
    description = description or self._html_search_meta(
        ["description", "og:description", "twitter:description"], webpage, "description", default=None
    )
    uploader_data = (
        get_first(media, ("owner", {dict}))
        or get_first(
            post, ("video", "creation_story", "attachments", ..., "media", lambda k, v: k == "owner" and v["name"])
        )
        or get_first(post, (..., "video", lambda k, v: k == "owner" and v["name"]))
        or get_first(post, ("node", "actors", ..., {dict}))
        or get_first(post, ("event", "event_creator", {dict}))
        or get_first(post, ("video", "creation_story", "short_form_video_context", "video_owner", {dict}))
        or {}
    )
    uploader = uploader_data.get("name") or (
        clean_html(get_element_by_id("fbPhotoPageAuthorName", webpage))
        or self._search_regex(
            (r'ownerName\s*:\s*"([^"]+)"', *self._og_regexes("title")), webpage, "uploader", fatal=False
        )
    )
    timestamp = int_or_none(self._search_regex(r'<abbr[^>]+data-utime=["\'](\d+)', webpage, "timestamp", default=None))
    thumbnail = self._html_search_meta(["og:image", "twitter:image"], webpage, "thumbnail", default=None)
    # some webpages contain unretrievable thumbnail urls
    # like https://lookaside.fbsbx.com/lookaside/crawler/media/?media_id=10155168902769113&get_thumbnail=1
    # in https://www.facebook.com/yaroslav.korpan/videos/1417995061575415/
    if thumbnail and not re.search(r"\.(?:jpg|png)", thumbnail):
        thumbnail = None
    info_dict = {
        "description": description,
        "uploader": uploader,
        "uploader_id": uploader_data.get("id"),
        "timestamp": timestamp,
        "thumbnail": thumbnail,
        "view_count": parse_count(
            self._search_regex(
                (r'\bviewCount\s*:\s*["\']([\d,.]+)', r'video_view_count["\']\s*:\s*(\d+)'),
                webpage,
                "view count",
                default=None,
            )
        ),
        "concurrent_view_count": get_first(
            post, (("video", (..., ..., "attachments", ..., "media")), "liveViewerCount", {int_or_none})
        ),
        **traverse_obj(
            post,
            (
                lambda _, v: video_id in v["url"],
                "feedback",
                {
                    "like_count": ("likers", "count", {int}),
                    "comment_count": ("total_comment_count", {int}),
                    "repost_count": ("share_count_reduced", {parse_count}),
                },
            ),
            get_all=False,
        ),
    }

    info_json_ld = self._search_json_ld(webpage, video_id, default={})
    info_json_ld["title"] = (
        re.sub(r"\s*\|\s*Facebook$", "", title or info_json_ld.get("title") or page_title or "")
        or (description or "").replace("\n", " ")
        or f"Facebook video #{video_id}"
    )
    return merge_dicts(info_json_ld, info_dict)


# DM 22nd Oct 26 - yt-dlp's FacebookIE says "Cannot parse data" when the page's relay data *is* the video (its "data"
# has videoDeliveryResponseFragment, but none of the video/event/nodes/node/mediaset keys yt-dlp looks for), eg
# https://www.facebook.com/100072539833653/videos/366781063112545/ - ~40 videos on the servers in Oct 26, which archived
# without their video. So find the video in the page, and download its formats with yt-dlp
VIDEO_ID_IN_URL = re.compile(r"/videos/(?:[^/?#]+/)?(\d+)|[?&]v=(\d+)")
RELAY_DATA = re.compile(r"data-sjs>(\{.*?\})</script>")


def _delivery_result(video: dict) -> dict:
    return traverse_obj(video, ("videoDeliveryResponseFragment", "videoDeliveryResponseResult", {dict})) or {}


def find_delivery_video(webpage: str, video_id: str | None) -> dict | None:
    """the video (with videoDeliveryResponseFragment) in the page's relay data with id video_id, or the only one if
    video_id isn't known (eg a /posts/ url). None if there isn't one"""
    videos = {}
    for raw in RELAY_DATA.findall(webpage):
        try:
            stack = [json.loads(raw)]
        except ValueError:
            continue
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if _delivery_result(node):
                    videos.setdefault(str(node.get("id") or node.get("videoId") or ""), node)
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    if video_id:
        return videos.get(video_id)
    return next(iter(videos.values())) if len(videos) == 1 else None


def delivery_video_info(ie: FacebookIE, video: dict, url: str, title: str | None = None) -> dict | None:
    """a yt-dlp info dict for the video, with its formats as yt-dlp's FacebookIE would build them. None if it has none"""
    result = _delivery_result(video)
    video_id = str(video.get("id") or video.get("videoId"))
    formats = []
    mpd_urls = traverse_obj(result, ("dash_manifest_urls", ..., "manifest_url", {url_or_none}))
    for idx, manifest in enumerate(traverse_obj(result, ("dash_manifests", lambda _, v: v["manifest_xml"]))):
        formats.extend(
            ie._parse_mpd_formats(
                compat_etree_fromstring(urllib.parse.unquote_plus(manifest["manifest_xml"])),
                mpd_url=traverse_obj(mpd_urls, idx),
            )
        )
    for progressive in traverse_obj(result, ("progressive_urls", lambda _, v: v["progressive_url"])):
        quality = traverse_obj(progressive, ("metadata", "quality", {str.lower}))
        formats.append(
            {
                "format_id": quality or "progressive",
                # sd, hd formats w/o resolution info are below DASH, as in yt-dlp
                "quality": {"sd": -3, "hd": -2}.get(quality, -3),
                "url": progressive["progressive_url"],
            }
        )
    if not formats:
        return None
    for f in formats:
        # as yt-dlp's FacebookIE: downloads with a browser's User-Agent are rate limited, and formats over ~500MB
        # return 403 unless the chunk size is limited
        f.setdefault("http_headers", {})["User-Agent"] = "facebookexternalhit/1.1"
        f.setdefault("downloader_options", {})["http_chunk_size"] = 250 << 20
    return {
        "id": video_id,
        "title": title or f"Facebook video #{video_id}",
        "formats": formats,
        "duration": float_or_none(video.get("playable_duration_in_ms"), 1000),
        "thumbnail": traverse_obj(video, ("preferred_thumbnail", "image", "uri"), ("thumbnailImage", "uri")),
        "webpage_url": url,
        "original_url": url,
        "extractor": ie.IE_NAME,
        "extractor_key": ie.ie_key(),
    }


class Facebook(GenericDropin):
    def extract_post(self, url: str, ie_instance: FacebookIE):
        post_id_regex = r"(?P<id>pfbid[A-Za-z0-9]+|\d+|t\.(\d+\/\d+))"
        post_id = re.search(post_id_regex, url).group("id")
        webpage = ie_instance._download_webpage(url.replace("://m.facebook.com/", "://www.facebook.com/"), post_id)

        # TODO: For long posts, this _extract_metadata only seems to return the first 100 or so characters, followed by ...

        # TODO: If/when https://github.com/yt-dlp/yt-dlp/pull/12275 is merged, uncomment next line and delete the one after
        # post_data = ie_instance._extract_metadata(webpage, post_id)
        post_data = _extract_metadata(ie_instance, webpage, post_id)

        video_id_match = VIDEO_ID_IN_URL.search(url)
        video_id = video_id_match and (video_id_match.group(1) or video_id_match.group(2))
        if video := find_delivery_video(webpage, video_id):
            # taken out again in create_metadata, so it isn't saved with the archive
            post_data["_delivery_video"] = video
        return post_data

    def create_metadata(self, post: dict, ie_instance: FacebookIE, archiver, url):
        result = Metadata()
        result.set_content(post.get("description", ""))
        result.set_title(post.get("title", ""))
        result.set("author", post.get("uploader", ""))
        result.set_url(url)
        if video := post.pop("_delivery_video", None):
            self._download_delivery_video(video, post, ie_instance, url, result)
        return result

    @staticmethod
    def _download_delivery_video(video: dict, post: dict, ie_instance: FacebookIE, url: str, result: Metadata) -> None:
        """downloads the video yt-dlp couldn't parse from the page, from its formats in the page's relay data"""
        info = delivery_video_info(ie_instance, video, url, post.get("title"))
        if not info:
            logger.warning(f"Facebook video in {url} has no formats in the page's video data")
            return
        ydl = ie_instance._downloader
        try:
            downloaded = ydl.process_ie_result(info, download=True) or info
        except MaxDownloadsReached:
            downloaded = info
        filename = ydl_entry_to_filename(ydl, downloaded)
        if not filename:
            logger.warning(f"Facebook video {info['id']} in {url}: couldn't download it from the page's video data")
            return
        media = Media(filename)
        for key in ("duration", "original_url"):
            if downloaded.get(key):
                media.set(key, downloaded[key])
        result.add_media(media)
        # for add_metadata, which downloads the thumbnail named after the video
        post["id"] = info["id"]
        post["thumbnail"] = info.get("thumbnail") or post.get("thumbnail")
        logger.info(f"Facebook video {info['id']}: yt-dlp couldn't parse {url}, downloaded it from the page's video data")

    def suitable(self, url, info_extractor: FacebookIE):
        # This Generic Dropin is causing problems with FB reels
        # Force it to ignore and just use the ytdlp InfoExtractor which works
        if "facebook.com/reel" in url:
            return False

        # possible invalid || - maybe | 
        regex = r"(?:https?://(?:[\w-]+\.)?(?:facebook\.com||facebookwkhpilnemxj7asaniu7vnjjbiltxjqhye3mhbshg7kx5tfyd\.onion)/)"
        return re.match(regex, url)

    def skip_ytdlp_download(self, url: str, is_instance: FacebookIE):
        """
        Skip using the ytdlp download method for Facebook *photo* posts, they have a URL with an id of t.XXXXX/XXXXX
        """
        if re.search(r"/t.\d+/\d+", url):
            return True
