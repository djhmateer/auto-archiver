import re
from urllib.parse import urlparse, urlunparse


class UrlUtil:
    telegram_private = re.compile(r"https:\/\/t\.me(\/c)\/(.+)\/(\d+)")
    is_istagram = re.compile(r"https:\/\/www\.instagram\.com")

    @staticmethod
    def clean(url: str) -> str: return url

    @staticmethod
    def is_auth_wall(url: str) -> bool:
        """
        checks if URL is behind an authentication wall meaning steps like wayback, wacz, ... may not work
        """
        if UrlUtil.telegram_private.match(url): return True
        if UrlUtil.is_istagram.match(url): return True

        return False

    @staticmethod
    def remove_get_parameters(url: str) -> str:
        # http://example.com/file.mp4?t=1 -> http://example.com/file.mp4
        # useful for mimetypes to work
        parsed_url = urlparse(url)
        new_url = urlunparse(parsed_url._replace(query=''))
        return new_url

    @staticmethod
    def is_relevant_url(url: str) -> bool:
        """
        Detect if a detected media URL is recurring and therefore irrelevant to a specific archive. Useful, for example, for the enumeration of the media files in WARC files which include profile pictures, favicons, etc.
        """
        clean_url = UrlUtil.remove_get_parameters(url)

        # favicons
        if "favicon" in url: return False
        # ifnore icons
        if clean_url.endswith(".ico"): return False
        # ignore SVGs
        if UrlUtil.remove_get_parameters(url).endswith(".svg"): return False

        # twitter profile pictures
        if "twimg.com/profile_images" in url: return False
        return True
