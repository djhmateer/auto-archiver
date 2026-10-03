from datetime import date

import pytest

from auto_archiver.modules.telethon_extractor.telethon_extractor import TelethonExtractor


@pytest.fixture(autouse=True)
def mock_client_setup(mocker):
    mocker.patch("telethon.client.auth.AuthMethods.start")


@pytest.fixture
def telethon_config(tmp_path):
    session_file = tmp_path / "test.session"
    session_file.touch()
    return session_file, {"telethon_extractor": {"session_file": str(session_file), "api_id": 123, "api_hash": "ABC"}}


def session_copies(session_file):
    return list(session_file.parent.glob("telethon-*.session"))


def test_setup_does_not_connect_or_copy_the_session(get_lazy_module, telethon_config, mocker):
    start = mocker.patch("telethon.client.auth.AuthMethods.start")
    session_file, config = telethon_config

    extractor = get_lazy_module("telethon_extractor").load(config)

    start.assert_not_called()
    assert session_copies(session_file) == []
    assert extractor.client is None


def test_cleanup_without_connecting_leaves_the_original_session(get_lazy_module, telethon_config):
    session_file, config = telethon_config

    get_lazy_module("telethon_extractor").load(config).cleanup()

    assert session_file.exists()


def test_non_telegram_url_does_not_connect(get_lazy_module, telethon_config, mocker):
    start = mocker.patch("telethon.client.auth.AuthMethods.start")
    session_file, config = telethon_config
    extractor = get_lazy_module("telethon_extractor").load(config)

    assert extractor.download(mocker_item("https://example.com/post/123")) is False
    start.assert_not_called()


def test_first_telegram_url_connects_once_and_cleanup_removes_the_copy(get_lazy_module, telethon_config, mocker):
    start = mocker.patch("telethon.client.auth.AuthMethods.start")
    mocker.patch("telethon.sync.TelegramClient.get_messages", return_value=None)
    session_file, config = telethon_config
    extractor = get_lazy_module("telethon_extractor").load(config)

    extractor.download(mocker_item("https://t.me/channel/1"))
    extractor.download(mocker_item("https://t.me/channel/2"))

    assert len(session_copies(session_file)) == 1
    assert f"telethon-{date.today().strftime('%Y-%m-%d')}" in extractor.session_copy
    assert start.call_count == 3  # the login check, then one per download
    extractor.cleanup()
    assert session_copies(session_file) == []
    assert session_file.exists()


def test_failed_login_raises_and_is_tried_again_on_the_next_url(get_lazy_module, telethon_config, mocker):
    start = mocker.patch("telethon.client.auth.AuthMethods.start", side_effect=Exception("Test exception"))
    disconnect = mocker.patch("telethon.sync.TelegramClient.disconnect")
    session_file, config = telethon_config
    extractor = get_lazy_module("telethon_extractor").load(config)

    with pytest.raises(Exception, match="Test exception"):
        extractor.download(mocker_item("https://t.me/channel/1"))
    with pytest.raises(Exception, match="Test exception"):
        extractor.download(mocker_item("https://t.me/channel/2"))

    assert start.call_count == 2
    assert disconnect.call_count == 2  # each failed client is closed, not leaked
    assert extractor.client is None
    assert len(session_copies(session_file)) == 1  # the copy is reused, not made again
    extractor.cleanup()
    assert session_copies(session_file) == []
    assert session_file.exists()


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://t.me/channel/123", True),
        ("https://t.me/c/123/456", True),
        ("https://t.me/channel/s/789", True),
        ("https://t.me/c/123/s/456", True),
        ("https://t.me/with_single/1234567?single", True),
        ("https://t.me/invalid", False),
        ("https://example.com/nottelegram/123", False),
    ],
)
def test_valid_url_regex(url, expected, get_lazy_module):
    match = TelethonExtractor.valid_url.search(url)
    assert bool(match) == expected


@pytest.mark.parametrize(
    "invite,expected",
    [
        ("t.me/joinchat/AAAAAE", True),
        ("t.me/+AAAAAE", True),
        ("t.me/AAAAAE", True),
        ("https://t.me/joinchat/AAAAAE", True),
        ("https://t.me/+AAAAAE", True),
        ("https://t.me/AAAAAE", True),
        ("https://example.com/AAAAAE", False),
    ],
)
def test_invite_pattern_regex(invite, expected, get_lazy_module):
    match = TelethonExtractor.invite_pattern.search(invite)
    assert bool(match) == expected


@pytest.fixture
def telethon_with_mock_client(mocker):
    extractor = TelethonExtractor.__new__(TelethonExtractor)
    extractor.client = mocker.MagicMock()
    extractor.client.get_messages.return_value = None
    return extractor


def test_private_channel_url_uses_marked_channel_id(telethon_with_mock_client):
    # t.me/c/<id> ids are channels - a bare positive int would be resolved by telethon as a PeerUser
    telethon_with_mock_client.download(mocker_item("https://t.me/c/1274414965/107212"))
    telethon_with_mock_client.client.get_messages.assert_called_once_with(-1001274414965, ids=107212)


def test_public_channel_url_uses_username(telethon_with_mock_client):
    telethon_with_mock_client.download(mocker_item("https://t.me/gwaramedia/62274"))
    telethon_with_mock_client.client.get_messages.assert_called_once_with("gwaramedia", ids=62274)
    telethon_with_mock_client.client.get_dialogs.assert_not_called()


def test_private_channel_not_cached_loads_dialogs_once(telethon_with_mock_client):
    client = telethon_with_mock_client.client
    client.get_input_entity.side_effect = ValueError("Could not find the input entity")

    telethon_with_mock_client.download(mocker_item("https://t.me/c/1274414965/107212"))
    telethon_with_mock_client.download(mocker_item("https://t.me/c/1274414965/107162"))

    client.get_dialogs.assert_called_once()


def test_private_channel_not_found_adds_status_note(telethon_with_mock_client):
    telethon_with_mock_client.client.get_messages.side_effect = ValueError("Could not find the input entity")
    item = mocker_item("https://t.me/c/1274414965/107959")

    assert telethon_with_mock_client.download(item) is False
    assert item.get_status_notes() == ["possible private channel issue - telegram account may not be a member"]


def test_public_channel_not_found_adds_no_status_note(telethon_with_mock_client):
    telethon_with_mock_client.client.get_messages.side_effect = ValueError("No user has that username")
    item = mocker_item("https://t.me/gwaramedia/62274")

    assert telethon_with_mock_client.download(item) is False
    assert item.get_status_notes() == []


def test_private_channel_already_cached_skips_dialogs(telethon_with_mock_client):
    telethon_with_mock_client.download(mocker_item("https://t.me/c/1274414965/107212"))
    telethon_with_mock_client.client.get_dialogs.assert_not_called()


def mocker_item(url):
    from auto_archiver.core import Metadata

    return Metadata().set_url(url)
