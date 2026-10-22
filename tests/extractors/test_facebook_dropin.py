"""
Tests for the Facebook dropin's fallback for videos yt-dlp says "Cannot parse data" for - nothing touches the network
"""

import json
from unittest.mock import Mock

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.extractor.facebook import FacebookIE

from auto_archiver.modules.generic_extractor.facebook import Facebook, delivery_video_info, find_delivery_video

VIDEO_ID = "366781063112545"
VIDEO_URL = f"https://www.facebook.com/100072539833653/videos/{VIDEO_ID}/"

# as the page's, trimmed: a 540x960 vp9 video and its audio
MANIFEST_XML = (
    '<?xml version="1.0" encoding="UTF-8"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" '
    'profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" minBufferTime="PT2S" type="static" '
    'mediaPresentationDuration="PT89.36068S"><Period id="0" duration="PT89.36068S">'
    '<AdaptationSet id="0" contentType="video" frameRate="25011/1000" subsegmentAlignment="true" par="9:16">'
    '<Representation id="2889900448053921v" bandwidth="884180" codecs="vp09.00.30.08.01.01.01.01.00" '
    'mimeType="video/mp4" sar="1:1" width="540" height="960">'
    "<BaseURL>https://scontent.xx.fbcdn.net/v/video_540p.mp4?oe=6ADFEE7E</BaseURL>"
    '<SegmentBase indexRange="818-1065" timescale="25011"><Initialization range="0-817"/></SegmentBase>'
    "</Representation></AdaptationSet>"
    '<AdaptationSet id="1" contentType="audio" subsegmentStartsWithSAP="1" subsegmentAlignment="true">'
    '<Representation id="2899991477044818a" bandwidth="64964" codecs="mp4a.40.5" mimeType="audio/mp4" '
    'audioSamplingRate="44100"><BaseURL>https://scontent.xx.fbcdn.net/v/audio.mp4?oe=6ADFEE7E</BaseURL>'
    '<SegmentBase indexRange="824-1395" timescale="44100"><Initialization range="0-823"/></SegmentBase>'
    "</Representation></AdaptationSet></Period></MPD>"
)


def _video(video_id: str, with_delivery: bool = True) -> dict:
    """the video as the page's relay data has it: the result's "data" is the video itself"""
    video = {
        "id": video_id,
        "playable_duration_in_ms": 89320,
        "url": f"https://www.facebook.com/100072539833653/videos/{video_id}/",
        "preferred_thumbnail": {"image": {"uri": f"https://scontent.xx.fbcdn.net/v/{video_id}_n.jpg?stp=dst-jpg"}},
        "videoDeliveryLegacyFields": None,
    }
    if with_delivery:
        video["videoDeliveryResponseFragment"] = {
            "videoDeliveryResponseResult": {
                "dash_manifests": [{"manifest_xml": MANIFEST_XML}],
                "dash_manifest_urls": [
                    {"manifest_url": f"https://www.facebook.com/dash_mpd_debug.mpd?v={video_id}&dummy=.mpd"}
                ],
                "progressive_urls": [
                    {"progressive_url": "https://scontent.xx.fbcdn.net/v/sd.mp4", "metadata": {"quality": "SD"}},
                    {"progressive_url": "https://scontent.xx.fbcdn.net/v/hd.mp4", "metadata": {"quality": "HD"}},
                ],
                "hls_playlist_urls": [],
                "id": video_id,
            }
        }
    return video


def _relay_script(data: dict) -> str:
    """a data-sjs script with the data nested as in the page: ScheduledServerJS > __bbox > RelayPrefetchedStreamCache"""
    relay = {
        "require": [
            [
                "ScheduledServerJS",
                "handle",
                None,
                [
                    {
                        "__bbox": {
                            "require": [
                                [
                                    "RelayPrefetchedStreamCache",
                                    "next",
                                    [],
                                    [
                                        "adp_CometTahoeRootQueryRelayPreloader_6ada1e",
                                        {"__bbox": {"result": {"data": data}}},
                                    ],
                                ]
                            ]
                        }
                    }
                ],
            ]
        ]
    }
    return f'<script type="application/json" nonce="x" data-content-len="1" data-sjs>{json.dumps(relay)}</script>'


def _page(*videos: dict) -> str:
    scripts = "".join(_relay_script(v) for v in videos)
    return (
        f"<html><head><title>Facebook</title></head><body>{scripts}<script data-sjs>{{not json}}</script></body></html>"
    )


@pytest.fixture
def ie():
    return FacebookIE(YoutubeDL({"quiet": True}))


def test_finds_the_video_with_the_urls_id():
    # eg a "more videos" one alongside it
    page = _page(_video("111"), _video(VIDEO_ID), _video("222", with_delivery=False))
    assert find_delivery_video(page, VIDEO_ID)["id"] == VIDEO_ID
    assert find_delivery_video(page, "999") is None


@pytest.mark.parametrize("videos, found", [((VIDEO_ID,), VIDEO_ID), ((VIDEO_ID, "111"), None), ((), None)])
def test_without_a_video_id_only_the_only_video(videos, found):
    video = find_delivery_video(_page(*(_video(v) for v in videos)), None)
    assert (video or {}).get("id") == found


def test_delivery_video_info_has_the_formats_yt_dlp_would(ie):
    info = delivery_video_info(ie, _video(VIDEO_ID), VIDEO_URL, "Mahmoud AlQayed")

    assert info["id"] == VIDEO_ID
    assert info["title"] == "Mahmoud AlQayed"
    assert info["duration"] == 89.32
    assert info["thumbnail"].startswith(f"https://scontent.xx.fbcdn.net/v/{VIDEO_ID}_n.jpg")
    assert info["extractor_key"] == "Facebook"
    formats = {f["format_id"]: f for f in info["formats"]}
    assert set(formats) == {"2889900448053921v", "2899991477044818a", "sd", "hd"}
    assert (formats["2889900448053921v"]["width"], formats["2889900448053921v"]["height"]) == (540, 960)
    assert formats["2899991477044818a"]["vcodec"] == "none"
    assert formats["hd"]["quality"] > formats["sd"]["quality"]
    assert all(f["http_headers"]["User-Agent"] == "facebookexternalhit/1.1" for f in info["formats"])


def test_delivery_video_info_without_formats(ie):
    video = _video(VIDEO_ID)
    video["videoDeliveryResponseFragment"]["videoDeliveryResponseResult"] = {"dash_manifests": [], "id": VIDEO_ID}
    assert delivery_video_info(ie, video, VIDEO_URL) is None


def test_delivery_video_info_title_fallback(ie):
    assert delivery_video_info(ie, _video(VIDEO_ID), VIDEO_URL)["title"] == f"Facebook video #{VIDEO_ID}"


def test_extract_post_keeps_the_video_for_create_metadata(ie):
    ie._download_webpage = Mock(return_value=_page(_video("111"), _video(VIDEO_ID)))

    post = Facebook().extract_post(VIDEO_URL, ie)

    assert post["_delivery_video"]["id"] == VIDEO_ID


def test_extract_post_without_a_video(ie):
    ie._download_webpage = Mock(return_value=_page(_video(VIDEO_ID, with_delivery=False)))

    assert "_delivery_video" not in Facebook().extract_post(VIDEO_URL, ie)


def test_create_metadata_downloads_the_video(tmp_path):
    video_file = tmp_path / f"{VIDEO_ID}.mp4"
    video_file.write_bytes(b"mp4")
    ydl = Mock()
    ydl.process_ie_result.side_effect = lambda info, download: {**info, "ext": "mp4"}
    ydl.prepare_filename.return_value = str(video_file)
    ie = FacebookIE(YoutubeDL({"quiet": True}))
    ie._downloader = ydl
    post = {"title": "Mahmoud AlQayed", "_delivery_video": _video(VIDEO_ID)}

    result = Facebook().create_metadata(post, ie, Mock(), VIDEO_URL)

    info = ydl.process_ie_result.call_args.args[0]
    assert info["id"] == VIDEO_ID and len(info["formats"]) == 4
    assert [m.filename for m in result.media] == [str(video_file)]
    assert result.media[0].get("duration") == 89.32
    # taken out, so it isn't saved with the archive; the id and thumbnail are for add_metadata's thumbnail download
    assert "_delivery_video" not in post
    assert post["id"] == VIDEO_ID
    assert post["thumbnail"].startswith(f"https://scontent.xx.fbcdn.net/v/{VIDEO_ID}_n.jpg")


def test_create_metadata_download_failure_keeps_the_post(tmp_path):
    ydl = Mock()
    # eg with ignoreerrors, a failed download
    ydl.process_ie_result.return_value = None
    ydl.prepare_filename.return_value = str(tmp_path / f"{VIDEO_ID}.mp4")
    ie = FacebookIE(YoutubeDL({"quiet": True}))
    ie._downloader = ydl
    post = {"title": "Mahmoud AlQayed", "_delivery_video": _video(VIDEO_ID)}

    result = Facebook().create_metadata(post, ie, Mock(), VIDEO_URL)

    assert result.media == []
    assert result.get_title() == "Mahmoud AlQayed"
    assert "id" not in post
