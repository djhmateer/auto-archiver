"""
Tests for the Al Jazeera dropin - yt-dlp is mocked, so nothing touches the network
"""

import pytest
from unittest.mock import Mock

from yt_dlp.extractor.aljazeera import AlJazeeraIE
from yt_dlp.extractor.brightcove import BrightcoveNewIE

from auto_archiver.core import Media, Metadata
from auto_archiver.modules.generic_extractor.aljazeera import Aljazeera, brightcove_urls

ARTICLE_URL = (
    "https://www.aljazeera.com/news/2025/8/31/israel-is-reducing-gaza-city-to-fields-of-rubble-kills-78-across-enclave"
)


def _video_js(video_id: str) -> str:
    # as in the article's body
    return (
        f'<video-js\n\t\t\t\t\t\t\t\tid="{video_id}"\n\t\t\t\t\t\t\t\tdata-video-id="{video_id}"'
        ' data-account="665003303001" data-player="6tKQRAx7lu" data-usage="cms:WordPress:7.0.7:2.8.6:javascript"'
        ' data-embed="default" class="video-js" data-application-id="" controls data-duration="1:45">'
    )


def _player_url(video_id: str) -> str:
    return f"https://players.brightcove.net/665003303001/6tKQRAx7lu_default/index.html?videoId={video_id}"


# a show's video page - yt-dlp's AlJazeeraIE doesn't match it
VIDEO_PAGE_URL = (
    "https://www.aljazeera.com/video/newsfeed/2025/5/19/video-netanyahu-says-military-will-take-control-of-gaza"
)

VIDEO_IDS = ["6377863811112", "6377937906112", "6377885321112", "6377948206112"]

ARTICLE = {
    "title": "Israel is reducing Gaza City to ‘fields of rubble’, kills 78 across enclave",
    "excerpt": "Once teeming and crowded with residential buildings, Gaza City is home to one million.",
    "date": "2025-08-31T11:31:24",
    "author": [{"name": "Al Jazeera Staff"}],
    # the api returns an empty featured video, which is what breaks yt-dlp's AlJazeeraIE
    "video": {"id": "", "accountId": None, "playerId": None},
    "content": "".join(f"<p>paragraph</p>\n<div>{_video_js(v)}</video-js></div>" for v in VIDEO_IDS),
}


def test_brightcove_urls_finds_every_video_in_the_body_in_page_order():
    assert brightcove_urls(ARTICLE) == [_player_url(v) for v in VIDEO_IDS]


def test_brightcove_urls_puts_featured_video_first_and_dedupes():
    article = {
        "video": {"id": "6377863811112", "accountId": "665003303001", "playerId": "6tKQRAx7lu"},
        "content": _video_js("6377863811112") + _video_js("6377885321112"),
    }
    assert brightcove_urls(article) == [_player_url("6377863811112"), _player_url("6377885321112")]


def test_brightcove_urls_featured_video_defaults_as_ytdlp():
    article = {"video": {"id": "123"}, "content": ""}
    assert brightcove_urls(article) == [
        "https://players.brightcove.net/911432371001/csvTfAlKW_default/index.html?videoId=123"
    ]


def test_brightcove_urls_article_without_videos():
    assert brightcove_urls({"video": None, "content": "<p>just text</p>"}) == []
    assert brightcove_urls({}) == []


def test_extract_post_queries_graphql_and_keeps_the_videos():
    ie = AlJazeeraIE()
    ie._download_json = Mock(return_value={"data": {"article": ARTICLE}})

    post = Aljazeera().extract_post(ARTICLE_URL, ie)

    url, display_id = ie._download_json.call_args[0]
    kwargs = ie._download_json.call_args[1]
    assert url == "https://www.aljazeera.com/graphql"
    assert display_id == "israel-is-reducing-gaza-city-to-fields-of-rubble-kills-78-across-enclave"
    assert kwargs["query"]["wp-site"] == "aje"
    assert '"postType": "news"' in kwargs["query"]["variables"]
    assert post == {
        "title": ARTICLE["title"],
        "description": ARTICLE["excerpt"],
        "date": ARTICLE["date"],
        "author": ["Al Jazeera Staff"],
        "video_urls": [_player_url(v) for v in VIDEO_IDS],
    }


def test_create_metadata_downloads_every_video_and_keeps_going_after_a_failure(tmp_path):
    def get_metadata_for_video(data, info_extractor, url, ydl):
        assert info_extractor is BrightcoveNewIE
        video_id = url.split("=")[-1]
        if video_id == VIDEO_IDS[1]:
            raise Exception("HTTP Error 403")
        if video_id == VIDEO_IDS[2]:
            return False
        video = Metadata()
        video.add_media(Media(str(tmp_path / f"{video_id}.mp4")))
        return video

    archiver = Mock(get_metadata_for_video=Mock(side_effect=get_metadata_for_video))
    ie = Mock(_downloader=Mock())
    post = {"date": ARTICLE["date"], "video_urls": [_player_url(v) for v in VIDEO_IDS]}

    result = Aljazeera().create_metadata(post, ie, archiver, ARTICLE_URL)

    assert archiver.get_metadata_for_video.call_count == 4
    assert [m.filename for m in result.media] == [
        str(tmp_path / f"{VIDEO_IDS[0]}.mp4"),
        str(tmp_path / f"{VIDEO_IDS[3]}.mp4"),
    ]
    assert result.get_url() == ARTICLE_URL
    assert result.get_timestamp() is not None


def test_dropin_is_found_for_the_aljazeera_extractor():
    from auto_archiver.modules.generic_extractor.generic_extractor import GenericExtractor

    assert isinstance(GenericExtractor().dropin_for_name(AlJazeeraIE.ie_key()), Aljazeera)


@pytest.mark.parametrize(
    "url, post_type",
    [
        (ARTICLE_URL, "news"),
        ("https://www.aljazeera.com/features/2025/8/31/some-feature", "post"),
        ("https://www.aljazeera.com/videos/2025/8/31/some-video", "video"),
        ("https://www.aljazeera.com/program/newsfeed/2025/8/31/some-episode", "episode"),
        (VIDEO_PAGE_URL, "episode"),
    ],
)
def test_extract_post_post_types(url, post_type):
    ie = AlJazeeraIE()
    ie._download_json = Mock(return_value={"data": {"article": {}}})

    Aljazeera().extract_post(url, ie)

    assert f'"postType": "{post_type}"' in ie._download_json.call_args[1]["query"]["variables"]


@pytest.mark.parametrize(
    "url, suitable",
    [
        (ARTICLE_URL, True),
        (VIDEO_PAGE_URL, True),
        ("https://www.aljazeera.com/where/palestine/", False),
        ("https://www.aljazeera.com/tag/israel-palestine-conflict/", False),
    ],
)
def test_suitable(url, suitable):
    assert Aljazeera().suitable(url, AlJazeeraIE) is suitable


def test_video_page_is_handled_by_the_dropin():
    """yt-dlp's AlJazeeraIE doesn't match it, so the dropin's suitable() is what routes it to AlJazeeraIE"""
    from auto_archiver.modules.generic_extractor.generic_extractor import GenericExtractor

    assert not AlJazeeraIE.suitable(VIDEO_PAGE_URL)
    assert "AlJazeera" in [ie.ie_key() for ie in GenericExtractor().suitable_extractors(VIDEO_PAGE_URL)]


def test_video_page_featured_video():
    """a video page's video is the episode's featured video, eg as in the page's apollo state"""
    episode = {
        "video": {"id": "6373056082112", "accountId": "665003303001", "playerId": "6tKQRAx7lu"},
        "content": "<p>Israeli Prime Minister Benjamin Netanyahu says...</p>",
    }
    assert brightcove_urls(episode) == [_player_url("6373056082112")]
