import errno
import json
import os
import re
import ssl
from typing import Type
from unittest.mock import Mock

import httplib2
import pytest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpMockSequence

from auto_archiver.core import Media
from auto_archiver.modules.gdrive_storage import GDriveStorage
from auto_archiver.modules.gdrive_storage.gdrive_storage import NUM_RETRIES
from auto_archiver.utils.custom_logger import logger
from tests.storages.test_storage_base import TestStorageBase


@pytest.fixture(autouse=True)
def mock_sleep(mocker):
    """Mock time.sleep to avoid delays."""
    return mocker.patch("time.sleep")


@pytest.fixture
def log_messages():
    """Collects gdrive_storage's log output (and what it forwards from googleapiclient) as 'LEVEL message' strings."""
    messages = []
    sink_id = logger.add(
        lambda m: messages.append(f"{m.record['level'].name} {m.record['message']}"),
        level="DEBUG",
        filter=lambda r: r["name"].endswith("gdrive_storage"),
    )
    yield messages
    logger.remove(sink_id)


@pytest.fixture
def gdrive_storage(setup_module, mocker) -> GDriveStorage:
    module_name: str = "gdrive_storage"
    config: dict = {
        "path_generator": "url",
        "filename_generator": "static",
        "root_folder_id": "fake_root_folder_id",
        "oauth_token": None,
        "service_account": "fake_service_account.json",
    }
    mocker.patch("google.oauth2.service_account.Credentials.from_service_account_file")
    return setup_module(module_name, config)


def test_initialize_fails_with_non_existent_creds(setup_module):
    """Test that the Google Drive service raises a FileNotFoundError when the service account file does not exist.
    (and isn't mocked)
    """
    config: dict = {
        "path_generator": "url",
        "filename_generator": "static",
        "root_folder_id": "fake_root_folder_id",
        "oauth_token": None,
        "service_account": "fake_service_account.json",
    }
    with pytest.raises(FileNotFoundError) as exc_info:
        setup_module("gdrive_storage", config)
    assert "No such file or directory" in str(exc_info.value)


def test_get_id_from_parent_and_name(gdrive_storage, mocker):
    """Test _get_id_from_parent_and_name returns correct id from an API result."""
    fake_list = mocker.MagicMock()
    fake_list.execute.return_value = {"files": [{"id": "123", "name": "testname"}]}
    fake_service = mocker.MagicMock()
    # mock the files.list return value
    fake_service.files.return_value.list.return_value = fake_list
    gdrive_storage.service = fake_service
    result = gdrive_storage._get_id_from_parent_and_name("parent", "mock", retries=1, use_mime_type=False)
    assert result == "123"


class FakeDrive:
    """
    Minimal in-memory stand-in for the Drive v3 service, enough for GDriveStorage: files().list(q=...) and
    files().create(...). Mimics Drive's eventually consistent search - anything created is invisible to list() for
    the next @search_lag list calls - and can raise queued errors from list()/create().
    """

    def __init__(self, search_lag: int = 0):
        self.items = []  # dicts: id, name, parent, is_folder, visible_after (list call count)
        self.search_lag = search_lag
        self.list_calls = 0
        self.created = []  # (name, parent, is_folder) in creation order
        self.list_errors = []  # exceptions raised by the next list() calls, in order
        self.create_errors = []  # (exception, still_create) for the next create() calls - still_create means the
        # file was created on Drive even though the call errored (eg a read timeout)
        self.num_retries_used = set()  # num_retries passed to every execute()
        self._next_id = 0

    def add_existing(self, name, parent, is_folder=False):
        return self._add(name, parent, is_folder, visible_after=0)

    def _add(self, name, parent, is_folder, visible_after):
        self._next_id += 1
        _id = f"{'folder' if is_folder else 'file'}_{self._next_id}"
        self.items.append(
            {"id": _id, "name": name, "parent": parent, "is_folder": is_folder, "visible_after": visible_after}
        )
        return _id

    def files(self):
        return self

    def list(self, q, **kwargs):
        def execute():
            self.list_calls += 1
            if self.list_errors:
                raise self.list_errors.pop(0)
            parent, name = re.search(r"'(.+?)' in parents and name = '(.+?)'", q).groups()
            folders_only = "mimeType" in q
            matches = [
                {"id": i["id"], "name": i["name"]}
                for i in self.items
                if i["parent"] == parent
                and i["name"] == name
                and (i["is_folder"] or not folders_only)
                and self.list_calls > i["visible_after"]
            ]
            return {"files": matches}

        return _Request(execute)

    def create(self, body, media_body=None, **kwargs):
        def execute():
            name, parent = body["name"][0], body["parents"][0]
            is_folder = body.get("mimeType") == "application/vnd.google-apps.folder"
            error, still_create = self.create_errors.pop(0) if self.create_errors else (None, False)
            if error and not still_create:
                raise error
            self.created.append((name, parent, is_folder))
            _id = self._add(name, parent, is_folder, visible_after=self.list_calls + self.search_lag)
            if error:
                raise error
            return {"id": _id}

        return _Request(execute)


class _Request:
    def __init__(self, execute):
        self._execute = execute

    def execute(self, num_retries=0):
        # FakeDrive errors stand for errors that are left after the client library's own retries
        _Request.drive_num_retries.add(num_retries)
        return self._execute()

    def next_chunk(self, num_retries=0):
        # uploads are sent with next_chunk and retried by _send_upload rather than the library (see the real client
        # tests below), so this isn't counted in drive_num_retries; the fake sends the whole file in one go
        return None, self._execute()


def http_error(status=500):
    return HttpError(resp=Mock(status=status, reason="error"), content=b"error")


def url_for(file_id):
    return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"


def media_with_key(key, tmp_path):
    local_file = tmp_path / os.path.basename(key)
    local_file.write_bytes(b"x" * 1000)
    media = Media(filename=str(local_file))
    media._key = key
    return media


@pytest.fixture
def drive(gdrive_storage, mocker) -> FakeDrive:
    """Plugs a FakeDrive with a 3-call search lag into gdrive_storage (so any search for something just created
    would miss it, as can happen in production)."""
    fake = FakeDrive(search_lag=3)
    _Request.drive_num_retries = fake.num_retries_used
    gdrive_storage.service = fake
    gdrive_storage.config = {"steps": {"storages": ["gdrive_storage"]}}
    mocker.patch("auto_archiver.modules.gdrive_storage.gdrive_storage.MediaFileUpload")
    return fake


def test_new_row_folder_created_once_without_sleeping(gdrive_storage, drive, mock_sleep, tmp_path):
    medias = [media_with_key(f"row-1/f{i}.jpg", tmp_path) for i in range(3)]
    for media in medias:
        gdrive_storage.store(media, "https://example.com")

    assert drive.created == [
        ("row-1", "fake_root_folder_id", True),
        ("f0.jpg", "folder_1", False),
        ("f1.jpg", "folder_1", False),
        ("f2.jpg", "folder_1", False),
    ]
    # urls come from the ids create() returned, not from searching (which would miss them due to the search lag)
    assert [m.urls for m in medias] == [[url_for("file_2")], [url_for("file_3")], [url_for("file_4")]]
    assert drive.list_calls == 1  # just the "does row-1 exist yet" check
    mock_sleep.assert_not_called()


def test_each_new_row_gets_its_own_folder(gdrive_storage, drive, tmp_path):
    for key in ["row-1/a.jpg", "row-2/a.jpg", "row-1/b.jpg"]:
        gdrive_storage.store(media_with_key(key, tmp_path), "https://example.com")

    assert [c for c in drive.created if c[2]] == [
        ("row-1", "fake_root_folder_id", True),
        ("row-2", "fake_root_folder_id", True),
    ]
    assert ("b.jpg", "folder_1", False) in drive.created  # back into row-1's folder, from the cache


def test_existing_folder_is_reused_not_recreated(gdrive_storage, drive, tmp_path):
    existing = drive.add_existing("row-1", "fake_root_folder_id", is_folder=True)

    gdrive_storage.store(media_with_key("row-1/a.jpg", tmp_path), "https://example.com")

    assert drive.created == [("a.jpg", existing, False)]


def test_existing_folder_still_hidden_by_search_lag_gets_a_duplicate(gdrive_storage, drive, tmp_path):
    """The accepted trade-off: re-archiving a row within minutes, while Drive's search can't see the folder from
    the first run yet, creates a second folder with the same name (rather than waiting ~90s on every new row)."""
    drive.items.append(
        {"id": "old_folder", "name": "row-1", "parent": "fake_root_folder_id", "is_folder": True, "visible_after": 99}
    )

    gdrive_storage.store(media_with_key("row-1/a.jpg", tmp_path), "https://example.com")

    assert drive.created[0] == ("row-1", "fake_root_folder_id", True)


def test_nested_folders_cached_per_parent(gdrive_storage, drive, tmp_path):
    for key in ["a/b/1.jpg", "a/b/2.jpg", "a/c/3.jpg"]:
        gdrive_storage.store(media_with_key(key, tmp_path), "https://example.com")

    folders = [c for c in drive.created if c[2]]
    assert folders == [("a", "fake_root_folder_id", True), ("b", "folder_1", True), ("c", "folder_1", True)]


def test_every_non_upload_api_call_uses_the_client_librarys_retries(gdrive_storage, drive, tmp_path):
    drive.add_existing("row-1", "fake_root_folder_id", is_folder=True)
    gdrive_storage.store(media_with_key("row-1/a.jpg", tmp_path), "https://example.com")  # list + create file
    gdrive_storage.store(media_with_key("row-2/a.jpg", tmp_path), "https://example.com")  # list + mkdir + create
    gdrive_storage.get_cdn_url(media_with_key("row-1/a.jpg", tmp_path))  # list

    assert drive.num_retries_used == {NUM_RETRIES}


def test_folder_search_error_left_after_library_retries_raises(gdrive_storage, drive, mock_sleep, tmp_path):
    drive.list_errors = [http_error(500)]

    with pytest.raises(HttpError):
        gdrive_storage.store(media_with_key("row-1/a.jpg", tmp_path), "https://example.com")
    assert drive.created == []
    mock_sleep.assert_not_called()


def test_failed_upload_raises_without_waiting(gdrive_storage, drive, mock_sleep, tmp_path):
    drive.create_errors = [(None, False), (http_error(400), False)]  # folder create ok, file create fails
    media = media_with_key("row-1/a.jpg", tmp_path)

    with pytest.raises(RuntimeError, match="upload failed for row-1/a.jpg"):
        gdrive_storage.store(media, "https://example.com")
    assert media.urls == []
    assert [c for c in drive.created if not c[2]] == []
    mock_sleep.assert_not_called()


def test_upload_that_errored_but_succeeded_is_not_duplicated(gdrive_storage, drive, mock_sleep, tmp_path):
    drive.search_lag = 0  # the post-error check has to be able to see the file
    drive.create_errors = [(None, False), (http_error(400), True)]  # file is created, but the call still errors
    media = media_with_key("row-1/a.jpg", tmp_path)

    gdrive_storage.store(media, "https://example.com")

    assert media.urls == [url_for("file_2")]
    assert [c for c in drive.created if not c[2]] == [("a.jpg", "folder_1", False)]
    mock_sleep.assert_not_called()


def test_failed_existence_check_after_failed_upload_is_reported_as_upload_failure(gdrive_storage, drive, tmp_path):
    drive.create_errors = [(None, False), (http_error(403), False)]
    original_list = drive.list

    def list_failing_after_the_folder_check(q, **kwargs):
        if drive.list_calls == 1:
            drive.list_errors = [http_error(403)]
        return original_list(q, **kwargs)

    drive.list = list_failing_after_the_folder_check

    assert gdrive_storage.upload(media_with_key("row-1/a.jpg", tmp_path)) is False


def test_missing_local_file_is_not_uploaded(gdrive_storage, drive, tmp_path):
    media = Media(filename=str(tmp_path / "gone.jpg"))
    media._key = "row-1/gone.jpg"

    assert gdrive_storage.upload(media) is False
    assert [c for c in drive.created if not c[2]] == []


def test_upload_returns_bool(gdrive_storage, drive, tmp_path):
    assert gdrive_storage.upload(media_with_key("row-1/a.jpg", tmp_path)) is True
    drive.create_errors = [(http_error(400), False)]
    assert gdrive_storage.upload(media_with_key("row-1/b.jpg", tmp_path)) is False


def test_duplicate_folders_are_warned_about(gdrive_storage, drive, tmp_path, log_messages):
    drive.add_existing("row-1", "fake_root_folder_id", is_folder=True)
    second = drive.add_existing("row-1", "fake_root_folder_id", is_folder=True)

    gdrive_storage.store(media_with_key("row-1/a.jpg", tmp_path), "https://example.com")

    assert drive.created == [("a.jpg", second, False)]
    assert any("WARNING" in m and "found 2 folders" in m for m in log_messages)


def test_one_debug_line_before_and_after_each_upload(gdrive_storage, drive, tmp_path, log_messages):
    gdrive_storage.store(media_with_key("row-1/a.jpg", tmp_path), "https://example.com")
    log_messages.clear()

    gdrive_storage.store(media_with_key("row-1/b.jpg", tmp_path), "https://example.com")

    assert len(log_messages) == 2
    assert "Uploading" in log_messages[0] and "(0.0 MB) as row-1/b.jpg" in log_messages[0]
    assert "Uploaded row-1/b.jpg (0.0 MB) in" in log_messages[1]


# the tests below go through the real googleapiclient (with its HTTP layer mocked) to check that it retries the
# transient errors we rely on it for, and not the others


class _HttpMockSequenceWithErrors(HttpMockSequence):
    """HttpMockSequence that raises any exception in the sequence instead of returning it, as httplib2 does for
    transport errors like a read timeout, and records each request's (method, headers)."""

    def __init__(self, iterable):
        super().__init__(iterable)
        self.requests = []

    def request(self, uri, method="GET", body=None, headers=None, *args, **kwargs):
        self.requests.append((method, headers or {}))
        if isinstance(self._iterable[0], Exception):
            raise self._iterable.pop(0)
        return super().request(uri, method, body, headers, *args, **kwargs)


@pytest.fixture
def real_client_storage(gdrive_storage):
    def with_responses(*responses):
        gdrive_storage.http = _HttpMockSequenceWithErrors(list(responses))
        gdrive_storage.service = build("drive", "v3", http=gdrive_storage.http, static_discovery=True)
        return gdrive_storage

    return with_responses


def _json(body):
    return json.dumps(body).encode()


@pytest.mark.parametrize("status", ["500", "503", "429"])
def test_library_retries_transient_errors(real_client_storage, mock_sleep, status):
    storage = real_client_storage(({"status": status}, b""), ({"status": "200"}, _json({"files": [{"id": "abc"}]})))

    assert storage._get_id_from_parent_and_name("parent", "name", retries=1) == "abc"
    assert mock_sleep.call_count == 1


def test_library_retries_rate_limit_403(real_client_storage, mock_sleep):
    rate_limited = _json({"error": {"errors": [{"reason": "userRateLimitExceeded"}], "code": 403}})
    storage = real_client_storage(({"status": "403"}, rate_limited), ({"status": "200"}, _json({"id": "folder"})))

    assert storage._mkdir("row-1", "parent") == "folder"
    assert mock_sleep.call_count == 1


@pytest.mark.parametrize("status", ["404", "400"])
def test_library_does_not_retry_permanent_errors(real_client_storage, mock_sleep, status):
    storage = real_client_storage(({"status": status}, b""))

    with pytest.raises(HttpError):
        storage._get_id_from_parent_and_name("parent", "name", retries=1)
    mock_sleep.assert_not_called()


def test_library_retries_a_failed_upload_within_the_same_upload_session(real_client_storage, mock_sleep, tmp_path):
    storage = real_client_storage(
        ({"status": "200"}, _json({"files": [{"id": "folder"}]})),  # row-1 folder exists
        ({"status": "200", "location": "https://upload.example/session"}, b""),  # resumable upload session started
        ({"status": "503"}, b""),  # sending the file fails
        ({"status": "200"}, _json({"id": "file_1"})),  # retried within the same session, so no duplicate
    )

    assert storage._upload(media_with_key("row-1/a.jpg", tmp_path)) == "file_1"
    assert mock_sleep.call_count == 1


FOLDER_EXISTS = ({"status": "200"}, _json({"files": [{"id": "folder"}]}))
SESSION_STARTED = ({"status": "200", "location": "https://upload.example/session"}, b"")
TIMEOUT = TimeoutError("The read operation timed out")
NOT_IN_DRIVE = ({"status": "200"}, _json({"files": []}))  # the check after a failed upload
RATE_LIMITED = ({"status": "403"}, _json({"error": {"errors": [{"reason": "userRateLimitExceeded"}], "code": 403}}))


def _sent_ranges(storage):
    return [h["Content-Range"] for m, h in storage.http.requests if m == "PUT" and "Content-Range" in h]


def test_upload_that_times_out_while_sending_the_file_is_resumed(
    real_client_storage, mock_sleep, tmp_path, log_messages
):
    """What happened in production: a rate limit 403, then the retry timed out - which the library doesn't retry."""
    storage = real_client_storage(
        FOLDER_EXISTS,
        SESSION_STARTED,
        RATE_LIMITED,
        TIMEOUT,
        ({"status": "308"}, b""),  # asked how much arrived: nothing, so the whole file is sent again
        ({"status": "200"}, _json({"id": "file_1"})),
    )

    assert storage._upload(media_with_key("row-1/a.jpg", tmp_path)) == "file_1"
    assert mock_sleep.call_count == 2
    assert any("WARNING" in m and "HTTP 403" in m and "retry 1 of" in m for m in log_messages)
    assert any("WARNING" in m and "TimeoutError" in m and "retry 2 of" in m for m in log_messages)


def test_resumed_upload_only_sends_the_part_that_didnt_arrive(real_client_storage, mock_sleep, tmp_path):
    storage = real_client_storage(
        FOLDER_EXISTS,
        SESSION_STARTED,
        TIMEOUT,
        ({"status": "308", "range": "bytes=0-599"}, b""),  # the first 600 of the 1000 bytes arrived
        ({"status": "200"}, _json({"id": "file_1"})),
    )

    assert storage._upload(media_with_key("row-1/a.jpg", tmp_path)) == "file_1"
    assert _sent_ranges(storage) == ["bytes 0-999/1000", "bytes */1000", "bytes 600-999/1000"]
    assert sum(1 for m, _ in storage.http.requests if m == "POST") == 1  # one upload session, so no duplicate file


def test_upload_that_arrived_but_whose_response_was_lost_is_not_sent_again(real_client_storage, mock_sleep, tmp_path):
    storage = real_client_storage(
        FOLDER_EXISTS,
        SESSION_STARTED,
        TIMEOUT,
        ({"status": "200"}, _json({"id": "file_1"})),  # asked how much arrived: all of it, the file is created
    )

    assert storage._upload(media_with_key("row-1/a.jpg", tmp_path)) == "file_1"
    assert _sent_ranges(storage) == ["bytes 0-999/1000", "bytes */1000"]


def test_transient_error_while_checking_how_much_arrived_is_retried(real_client_storage, mock_sleep, tmp_path):
    storage = real_client_storage(
        FOLDER_EXISTS,
        SESSION_STARTED,
        TIMEOUT,
        ({"status": "503"}, b""),  # the "how much arrived" check fails too
        ({"status": "308"}, b""),
        ({"status": "200"}, _json({"id": "file_1"})),
    )

    assert storage._upload(media_with_key("row-1/a.jpg", tmp_path)) == "file_1"
    assert mock_sleep.call_count == 2


def test_upload_session_that_fails_to_start_is_retried_without_nested_retries(
    real_client_storage, mock_sleep, tmp_path
):
    storage = real_client_storage(
        FOLDER_EXISTS,
        *[TIMEOUT] * (NUM_RETRIES + 1),  # starting the session never works
        NOT_IN_DRIVE,
    )

    assert storage._upload(media_with_key("row-1/a.jpg", tmp_path)) is None
    # one attempt per retry - not the library's NUM_RETRIES retries inside each of ours
    assert sum(1 for m, _ in storage.http.requests if m == "POST") == NUM_RETRIES + 1
    assert mock_sleep.call_count == NUM_RETRIES


def test_upload_that_keeps_timing_out_gives_up_after_num_retries(real_client_storage, mock_sleep, tmp_path):
    storage = real_client_storage(FOLDER_EXISTS, SESSION_STARTED, *[TIMEOUT] * (NUM_RETRIES + 1), NOT_IN_DRIVE)

    assert storage._upload(media_with_key("row-1/a.jpg", tmp_path)) is None
    assert mock_sleep.call_count == NUM_RETRIES


@pytest.mark.parametrize(
    "error",
    [
        ({"status": "404"}, b""),  # the upload session has expired
        ({"status": "403"}, _json({"error": {"errors": [{"reason": "insufficientPermissions"}], "code": 403}})),
    ],
)
def test_permanent_error_while_resuming_is_not_retried(real_client_storage, mock_sleep, tmp_path, error):
    storage = real_client_storage(FOLDER_EXISTS, SESSION_STARTED, TIMEOUT, error, NOT_IN_DRIVE)

    assert storage._upload(media_with_key("row-1/a.jpg", tmp_path)) is None
    assert mock_sleep.call_count == 1  # just the one after the timeout


def test_non_network_os_error_is_not_retried(real_client_storage, mock_sleep, tmp_path):
    storage = real_client_storage(
        FOLDER_EXISTS, SESSION_STARTED, PermissionError(errno.EACCES, "Permission denied"), NOT_IN_DRIVE
    )

    assert storage._upload(media_with_key("row-1/a.jpg", tmp_path)) is None
    mock_sleep.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        TIMEOUT,
        ConnectionResetError(errno.ECONNRESET, "reset"),
        OSError(errno.EPIPE, "Broken pipe"),
        ssl.SSLError("bad record mac"),
        httplib2.ServerNotFoundError("no dns"),
    ],
)
def test_network_errors_are_retried(real_client_storage, mock_sleep, tmp_path, error):
    storage = real_client_storage(
        FOLDER_EXISTS, SESSION_STARTED, error, ({"status": "308"}, b""), ({"status": "200"}, _json({"id": "f"}))
    )

    assert storage._upload(media_with_key("row-1/a.jpg", tmp_path)) == "f"


def test_library_retry_messages_reach_our_logs(real_client_storage, log_messages):
    storage = real_client_storage(({"status": "500"}, b""), ({"status": "200"}, _json({"files": []})))

    storage._get_id_from_parent_and_name("parent", "name", retries=1, raise_on_missing=False)

    assert any(
        "WARNING" in m and "googleapiclient.http" in m and f"retry 1 of {NUM_RETRIES}" in m for m in log_messages
    )


def test_get_cdn_url_searches_for_files_not_uploaded_by_this_process(gdrive_storage, drive, tmp_path):
    folder = drive.add_existing("row-1", "fake_root_folder_id", is_folder=True)
    file_id = drive.add_existing("a.jpg", folder)

    assert gdrive_storage.get_cdn_url(media_with_key("row-1/a.jpg", tmp_path)) == url_for(file_id)


def test_already_stored_media_is_skipped(gdrive_storage, drive, tmp_path):
    media = media_with_key("row-1/a.jpg", tmp_path)
    media.add_url("https://already/there")

    gdrive_storage.store(media, "https://example.com")

    assert drive.created == []


def test_path_parts():
    media = Media(filename="test.jpg")
    media._key = "folder1/folder2/test.jpg"


@pytest.mark.skip(reason="Requires real credentials")
@pytest.mark.download
class TestGDriveStorageConnected(TestStorageBase):
    """
    'Real' tests for GDriveStorage.
    """

    module_name: str = "gdrive_storage"
    storage: Type[GDriveStorage]
    config: dict = {
        "path_generator": "url",
        "filename_generator": "static",
        # TODO: replace with real root folder id
        "root_folder_id": "1TVY_oJt95_dmRSEdP9m5zFy7l50TeCSk",
        "oauth_token": None,
        "service_account": "secrets/service_account.json",
    }

    def test_initialize_with_real_credentials(self):
        """
        Test that the Google Drive service can be initialized with real credentials.
        """
        assert self.storage.service is not None
