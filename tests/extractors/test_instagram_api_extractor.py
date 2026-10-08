from datetime import datetime
import math

import pytest

from auto_archiver.core import Metadata
from auto_archiver.modules.instagram_api_extractor.instagram_api_extractor import InstagramAPIExtractor
from .test_extractor_base import TestExtractorBase


@pytest.fixture
def mock_user_response():
    return {
        "user": {
            "pk": "123",
            "username": "test_user",
            "full_name": "Test User",
            "profile_pic_url_hd": "http://example.com/profile.jpg",
            "profile_pic_url": "http://example.com/profile_lowres.jpg",
        }
    }


@pytest.fixture
def mock_post_response():
    return {
        "id": "post_123",
        "code": "abc123",
        "caption_text": "Test Caption",
        "taken_at": datetime.now().timestamp(),
        "video_url": "http://example.com/video.mp4",
        "thumbnail_url": "http://example.com/thumbnail.jpg",
    }


@pytest.fixture
def mock_story_response():
    return [{"id": "story_123", "taken_at": datetime.now().timestamp(), "video_url": "http://example.com/story.mp4"}]


@pytest.fixture
def mock_highlight_response():
    return {
        "response": {
            "reels": {
                "highlight:123": {
                    "id": "123",
                    "title": "Test Highlight",
                    "items": [
                        {
                            "id": "item_123",
                            "taken_at": datetime.now().timestamp(),
                            "video_url": "http://example.com/highlight.mp4",
                        }
                    ],
                }
            }
        }
    }


# @pytest.mark.incremental
class TestInstagramAPIExtractor(TestExtractorBase):
    """
    Test suite for InstagramAPIExtractor.
    """

    extractor_module = "instagram_api_extractor"
    extractor: InstagramAPIExtractor

    config = {
        "access_token": "test_access_token",
        "api_endpoint": "https://api.instagram.com/v1",
        "full_profile": False,
        # "full_profile_max_posts": 0,
        # "minimize_json_output": True,
    }

    @pytest.fixture
    def metadata(self):
        m = Metadata()
        m.set_url("https://instagram.com/test_user")
        m.set("netloc", "instagram.com")
        return m

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://instagram.com/user", [("", "user", "")]),
            # usernames starting with a post prefix are profiles, not posts
            ("https://www.instagram.com/paltimesnews", [("", "paltimesnews", "")]),
            ("https://www.instagram.com/reelsfan/", [("", "reelsfan", "")]),
            ("https://instagr.am/p/post_id", []),
            ("https://youtube.com", []),
            ("https://www.instagram.com/p/C9QmUGDNOd0/", [("p", "C9QmUGDNOd0", "")]),
            ("https://www.instagram.com/reel/reel_id", [("reel", "reel_id", "")]),
            ("https://instagram.com/stories/highlights/123", [("stories/highlights", "123", "")]),
            ("https://instagram.com/stories/user/123", [("stories", "user", "123")]),
        ],
    )
    def test_url_parsing(self, url, expected):
        assert self.extractor.valid_url.findall(url) == expected

    @pytest.mark.parametrize(
        "url",
        ["https://www.instagram.com/reels/C9QmUGDNOd0/", "https://instagram.com/reels/C9QmUGDNOd0?igsh=abc"],
    )
    def test_sanitize_url_rejects_reels(self, url):
        with pytest.raises(AssertionError, match=r"use /p/ instead eg https://www.instagram.com/p/C9QmUGDNOd0/"):
            self.extractor.sanitize_url(url)

    def test_sanitize_url_leaves_other_urls(self):
        url = "https://www.instagram.com/reel/C9QmUGDNOd0/"
        assert self.extractor.sanitize_url(url) == url

    def test_initialize(self):
        assert self.extractor.api_endpoint[-1] != "/"

    def test_call_api_sends_access_token_header(self, mocker):
        """The configured access_token must be sent as the x-access-key header the API server authenticates."""
        mock_get = mocker.patch("auto_archiver.modules.instagram_api_extractor.instagram_api_extractor.requests.get")
        self.extractor.call_api("v2/user/by/username", {"username": "test_user"})
        mock_get.assert_called_once_with(
            f"{self.extractor.api_endpoint}/v2/user/by/username",
            headers={"accept": "application/json", "x-access-key": "test_access_token"},
            params={"username": "test_user"},
            timeout=60,
        )

    @pytest.mark.parametrize(
        "input_dict,expected",
        [
            ({"x": 0, "valid": "data"}, {"valid": "data"}),
            ({"nested": {"y": None, "valid": [{}]}}, {"nested": {"valid": [{}]}}),
        ],
    )
    def test_cleanup_dict(self, input_dict, expected):
        assert self.extractor.cleanup_dict(input_dict) == expected

    def test_download(self):
        pass

    def test_download_post(self, metadata, mock_user_response):
        # test with context=reel
        # test with context=post
        # test with multiple images
        # test gets text (metadata title)
        pass

    def test_download_profile_basic(self, metadata, mock_user_response, mocker):
        """Test basic profile download without full_profile"""
        mock_call = mocker.patch.object(self.extractor, "call_api")
        mock_download = mocker.patch.object(self.extractor, "download_from_url")
        # Mock API responses
        mock_call.return_value = mock_user_response
        mock_download.return_value = "profile.jpg"

        result = self.extractor.download_profile(metadata, "test_user")
        assert result.status == "insta profile: success"
        assert result.get_title() == "Test User"
        assert result.get("data") == self.extractor.cleanup_dict(mock_user_response["user"])
        # Verify profile picture download
        mock_call.assert_called_once_with("v2/user/by/username", {"username": "test_user"})
        mock_download.assert_called_once_with("http://example.com/profile.jpg")
        assert len(result.media) == 1
        assert result.media[0].filename == "profile.jpg"

    def test_download_profile_full(self, metadata, mock_user_response, mock_story_response, mocker):
        """Test full profile download with stories/posts"""
        mock_call = mocker.patch.object(self.extractor, "call_api")
        mock_posts = mocker.patch.object(self.extractor, "download_all_posts")
        mock_highlights = mocker.patch.object(self.extractor, "download_all_highlights")
        mock_tagged = mocker.patch.object(self.extractor, "download_all_tagged")
        mock_stories = mocker.patch.object(self.extractor, "_download_stories_reusable")

        self.extractor.full_profile = True
        mock_call.side_effect = [mock_user_response, mock_story_response]
        mock_highlights.return_value = 1
        mock_stories.return_value = mock_story_response
        mock_posts.return_value = 2
        mock_tagged.return_value = 3

        result = self.extractor.download_profile(metadata, "test_user")
        assert result.get("#stories") == len(mock_story_response)
        mock_posts.assert_called_once_with(result, "123", max_to_download=math.inf)
        assert "errors" not in result.metadata

    def test_download_profile_not_found(self, metadata, mocker):
        """Test profile not found error"""
        mock_call = mocker.patch.object(self.extractor, "call_api")
        mock_call.return_value = {"user": None}
        with pytest.raises(AssertionError) as exc_info:
            self.extractor.download_profile(metadata, "invalid_user")
        assert "User invalid_user not found" in str(exc_info.value)

    def test_download_profile_error_handling(self, metadata, mock_user_response, mocker):
        """Test error handling in full profile mode"""
        mock_call = mocker.patch.object(self.extractor, "call_api")
        mock_highlights = mocker.patch.object(self.extractor, "download_all_highlights")
        mock_tagged = mocker.patch.object(self.extractor, "download_all_tagged")
        stories_tagged = mocker.patch.object(self.extractor, "_download_stories_reusable")
        mock_posts = mocker.patch.object(self.extractor, "download_all_posts")

        self.extractor.full_profile = True
        mock_call.side_effect = [mock_user_response, Exception("Stories API failed"), Exception("Posts API failed")]
        mock_highlights.return_value = 1
        mock_tagged.return_value = 2
        stories_tagged.return_value = None
        mock_posts.return_value = 4
        result = self.extractor.download_profile(metadata, "test_user")

        assert result.is_success()
        assert "Error downloading stories for test_user" in result.metadata["errors"]

    def test_carousel_item_failure_skips_only_that_item(self, mocker):
        """A carousel item whose video fails (eg a CDN 429) is skipped, the rest of the post is kept and noted"""
        post = {
            "id": "post_1",
            "code": "abc123",
            "carousel_media": [
                {"id": "c1", "thumbnail_url": "http://example.com/c1.jpg"},
                {"id": "c2", "video_url": "http://example.com/c2.mp4", "thumbnail_url": "http://example.com/c2.jpg"},
                {"id": "c3", "thumbnail_url": "http://example.com/c3.jpg"},
            ],
        }
        mocker.patch.object(self.extractor, "call_api", return_value=post)
        mocker.patch.object(
            self.extractor,
            "download_from_url",
            side_effect=lambda url, verbose=True: None if url.endswith(".mp4") else url.rsplit("/", 1)[-1],
        )
        item = Metadata().set_url("https://www.instagram.com/p/abc123/")

        result = self.extractor.download(item)

        assert result.is_success()
        assert len(result.media) == 1
        assert result.media[0].filename == "c1.jpg"
        assert [m.filename for m in result.media[0].get("other media")] == ["c3.jpg"]
        assert "Error downloading carousel item c2" in result.get("errors")
        assert result.get_status_notes() == ["1 Instagram item(s) failed to download - see logs"]

    def test_skipped_profile_posts_are_noted_and_counter_resets(self, mocker):
        """Posts skipped during a full profile download are counted in the status note, per download() call"""
        self.extractor.full_profile = True
        mocker.patch.object(self.extractor, "_download_stories_reusable", return_value=[])
        mocker.patch.object(self.extractor, "download_all_tagged", return_value=0)
        mocker.patch.object(self.extractor, "download_all_highlights", return_value=0)
        mocker.patch.object(self.extractor, "download_from_url", return_value=None)
        posts = [{"id": "p1", "video_url": "http://example.com/1.mp4"}, {"id": "p2", "video_url": "http://example.com/2.mp4"}]
        mocker.patch.object(
            self.extractor,
            "call_api",
            side_effect=lambda path, params: {"user": {"pk": "123", "username": "u"}}
            if path == "v2/user/by/username"
            else [posts, ""],
        )

        result = self.extractor.download(Metadata().set_url("https://www.instagram.com/test_user/"))
        assert result.get_status_notes() == ["2 Instagram item(s) failed to download - see logs"]

        # nothing fails on the next row, so no stale note carried over
        self.extractor.full_profile = False
        clean = self.extractor.download(Metadata().set_url("https://www.instagram.com/test_user/"))
        assert clean.get_status_notes() == []
