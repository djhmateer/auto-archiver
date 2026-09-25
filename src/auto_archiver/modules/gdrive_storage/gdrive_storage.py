import errno
import json
import logging
import os
import random
import ssl
import time
from typing import IO

import httplib2
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# private, but it's the library's own "is this error status worth retrying" check (5xx, 429, rate limit 403s), which
# _send_upload has to apply itself. The lock file pins the library, and the tests fail if this ever disappears
from googleapiclient.http import _should_retry_response
from auto_archiver.utils.custom_logger import logger

from auto_archiver.core import Media, Metadata
from auto_archiver.core import Storage

# Every Drive API call is made with execute(num_retries=NUM_RETRIES): the client library then retries only transient
# failures (5xx, 429, rate limit 403s, timeouts and dropped connections) with exponential backoff, and fails straight
# away on anything else (eg 404, permission 403, 400). The wait before retry n is random() * 2^n seconds, so 7 retries
# wait ~2 minutes in total on average (4 at most) - long enough to get past a rate limit window, as the old fixed
# 3 x 30s sleeps were.
# File uploads are the exception: the library's retries don't cover all of a resumable upload, so _send_upload
# retries those itself, with the same budget and backoff
NUM_RETRIES = 7


class _ForwardToLoguru(logging.Handler):
    """The client library reports its retries through stdlib logging, which doesn't reach our log files."""

    def emit(self, record: logging.LogRecord) -> None:
        logger.log(record.levelname, f"{record.name}: {record.getMessage()}")


def _forward_googleapiclient_logs() -> None:
    lib_logger = logging.getLogger("googleapiclient.http")
    if not any(isinstance(h, _ForwardToLoguru) for h in lib_logger.handlers):
        lib_logger.addHandler(_ForwardToLoguru())
        lib_logger.propagate = False


# the errno names the client library retries a failed request on (see googleapiclient.http._retry_request)
_TRANSIENT_ERRNOS = {"WSAETIMEDOUT", "ETIMEDOUT", "EPIPE", "ECONNABORTED", "ECONNREFUSED", "ECONNRESET"}


def _is_transient(e: Exception) -> bool:
    """Whether a failed Drive request is worth retrying, by the same rules as the client library."""
    if isinstance(e, HttpError):
        return _should_retry_response(e.resp.status, e.content)
    if isinstance(e, (ssl.SSLError, TimeoutError, ConnectionError, httplib2.ServerNotFoundError)):
        return True
    return isinstance(e, OSError) and errno.errorcode.get(e.errno) in _TRANSIENT_ERRNOS


def _describe(e: Exception) -> str:
    """eg 'HTTP 403 userRateLimitExceeded' - the reason is what tells a rate limit from a permissions problem"""
    if not isinstance(e, HttpError):
        return repr(e)
    # read from the body as _should_retry_response does: e.error_details is only filled in if Drive sent a message
    try:
        errors = json.loads(e.content)["error"]["errors"]
        reasons = [err["reason"] for err in errors if err.get("reason")]
    except (ValueError, KeyError, TypeError, AttributeError):
        reasons = []
    return f"HTTP {e.resp.status} {', '.join(reasons) or e.reason}"


def _megabytes(path: str) -> str:
    return f"{os.path.getsize(path) / 1_000_000:.1f} MB"


class GDriveStorage(Storage):
    def setup(self) -> None:
        self.scopes = ["https://www.googleapis.com/auth/drive"]
        # DM 23rd Sep 26 - Drive's search (files().list) can take minutes to see a newly created folder, but the id
        # create() returns is usable straight away - so remember folder ids rather than searching for them again.
        # Only lives for this process, so a folder deleted in Drive mid-run isn't noticed until the next run
        self._folder_ids: dict[tuple[str, str], str] = {}  # (parent_id, folder name) -> folder id
        _forward_googleapiclient_logs()
        # Initialize Google Drive service
        self._setup_google_drive_service()

    def _setup_google_drive_service(self):
        """Initialize Google Drive service based on provided credentials."""
        if self.oauth_token:
            logger.debug(f"Using Google Drive OAuth token: {self.oauth_token}")
            self.service = self._initialize_with_oauth_token()
        elif self.service_account:
            logger.debug(f"Using Google Drive service account: {self.service_account}")
            self.service = self._initialize_with_service_account()
        else:
            raise ValueError("Missing credentials: either `oauth_token` or `service_account` must be provided.")

    def _initialize_with_oauth_token(self):
        """Initialize Google Drive service with OAuth token."""
        with open(self.oauth_token, "r") as stream:
            creds_json = json.load(stream)
            creds_json["refresh_token"] = creds_json.get("refresh_token", "")

        creds = Credentials.from_authorized_user_info(creds_json, self.scopes)
        if not creds.valid and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(self.oauth_token, "w") as token_file:
                logger.debug("Saving refreshed OAuth token.")
                token_file.write(creds.to_json())
        elif not creds.valid:
            raise ValueError("Invalid OAuth token. Please regenerate the token.")

        return build("drive", "v3", credentials=creds)

    def _initialize_with_service_account(self):
        """Initialize Google Drive service with service account."""
        creds = service_account.Credentials.from_service_account_file(self.service_account, scopes=self.scopes)
        return build("drive", "v3", credentials=creds)

    def get_cdn_url(self, media: Media) -> str:
        """
        only support files saved in a folder for GD
        S3 supports folder and all stored in the root
        Not used by store() (which has the id from the upload), so this only runs if called directly
        """
        parent_id, folder_id = self.root_folder_id, None
        path_parts = media.key.split(os.path.sep)
        filename = path_parts[-1]
        for folder in path_parts[0:-1]:
            folder_id = self._get_folder_id(parent_id, folder, create=False)
            parent_id = folder_id
        # get id of file inside folder (or sub folder)
        file_id = self._get_id_from_parent_and_name(folder_id, filename, raise_on_missing=True)
        if not file_id:
            #
            logger.info(f"File {filename} not found in folder {folder_id}")
            return None
        return self._file_url(file_id)

    @staticmethod
    def _file_url(file_id: str) -> str:
        return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"

    def store(self, media: Media, url: str, metadata: Metadata = None) -> None:
        """
        Same as Storage.store, but builds the url from the id the upload returned rather than searching Drive for
        the file we've just uploaded (search can take minutes to see new files).
        Raises if the upload failed (Metadata.store logs it and carries on with the other media).
        """
        if media.is_stored(in_storage=self):
            logger.debug(f"{media.key} already stored, skipping")
            return

        self.set_key(media, url, metadata)
        file_id = self._upload(media)
        if not file_id:
            raise RuntimeError(f"Google Drive upload failed for {media.key} (see previous error)")
        media.add_url(self._file_url(file_id))

    def upload(self, media: Media, **kwargs) -> bool:
        return self._upload(media) is not None

    def _upload(self, media: Media) -> str | None:
        """
        Uploads the file to root_folder_id/<folders in media.key>/<filename>, creating any missing folders.
        Returns the uploaded file's id, or None if it couldn't be uploaded
        """
        path_parts = media.key.split(os.path.sep)
        filename = path_parts[-1]
        parent_id = self.root_folder_id
        for folder in path_parts[0:-1]:
            parent_id = self._get_folder_id(parent_id, folder, create=True)

        try:
            size = _megabytes(media.filename)
            media_body = MediaFileUpload(media.filename, resumable=True)
        except FileNotFoundError as e:
            logger.error(f"Can't upload {media.key}, local file is missing: {e}")
            return None

        logger.debug(f"Uploading {media.filename} ({size}) as {media.key}")
        started = time.monotonic()
        try:
            request = self.service.files().create(
                supportsAllDrives=True,
                body={"name": [filename], "parents": [parent_id]},
                media_body=media_body,
                fields="id",
            )
            gd_file = self._send_upload(request, media.key)
        except Exception as e:
            # transient errors have already been retried (see _send_upload). The file may still have been created
            # if only the response was lost, so check before reporting a failure
            if existing_id := self._find_after_failed_upload(parent_id, filename):
                logger.warning(
                    f"Upload of {media.key} errored but the file is in Drive as {existing_id}, using it: {e}"
                )
                return existing_id
            logger.error(f"Failed to upload {media.filename} as {media.key}: {e}")
            return None

        logger.debug(f"Uploaded {media.key} ({size}) in {time.monotonic() - started:.1f}s as {gd_file['id']}")
        return gd_file["id"]

    def _send_upload(self, request, key: str) -> dict:
        """
        Sends a resumable upload, doing all the retrying itself: request.execute(num_retries=...) retries an error
        status, but gives up at once if sending the file throws (eg 'The read operation timed out' - seen in
        production), and never retries the "how much of the file arrived?" check it makes before resuming.

        next_chunk() is called without the library's own retries, so there is one retry budget (NUM_RETRIES, with the
        library's backoff) rather than retries nested inside retries. After a failure, the next next_chunk() carries
        on from where the upload got to: it starts the session again if that never started, and otherwise asks Drive
        how much arrived and sends the rest in the same session - so a retry can't create a duplicate file, and a
        file that did arrive but whose response was lost is returned rather than sent again.
        """
        failures = 0
        while True:
            try:
                _, gd_file = request.next_chunk(num_retries=0)
                if gd_file is not None:
                    return gd_file
            except Exception as e:
                failures += 1
                if not _is_transient(e) or failures > NUM_RETRIES:
                    raise
                wait = random.random() * 2**failures
                logger.warning(
                    f"Sending {key} to Drive failed ({_describe(e)}), resuming the upload in {wait:.1f}s "
                    f"(retry {failures} of {NUM_RETRIES})"
                )
                time.sleep(wait)

    def _find_after_failed_upload(self, parent_id: str, filename: str) -> str | None:
        try:
            return self._get_id_from_parent_and_name(parent_id, filename, retries=1, raise_on_missing=False)
        except HttpError as e:
            logger.warning(f"Couldn't check whether {filename} reached Drive after a failed upload: {e}")
            return None

    # must be implemented even if unused
    def uploadf(self, file: IO[bytes], key: str, **kwargs: dict) -> bool:
        pass

    def _get_id_from_parent_and_name(
        self,
        parent_id: str,
        name: str,
        retries: int = 4,
        sleep_seconds: int = 30,
        use_mime_type: bool = False,
        raise_on_missing: bool = True,
    ):
        """
        Retrieves the id of a folder or file from its @name and the @parent_id folder
        @retries is how many times to search for it, sleeping @sleep_seconds in between - for something recently
        created that Drive's search may not see yet. API errors are retried separately (see NUM_RETRIES) and raise
        HttpError if they persist
        If @use_mime_type will restrict search to "mimeType='application/vnd.google-apps.folder'"
        If @raise_on_missing will throw error when not found, or returns None
        Returns the id of the file or folder from its name as a string
        """
        debug_header: str = f"[searching {name=} in {parent_id=}]"
        query_string = f"'{parent_id}' in parents and name = '{name}' and trashed = false "
        if use_mime_type:
            query_string += " and mimeType='application/vnd.google-apps.folder' "

        for attempt in range(retries):
            results = (
                self.service.files()
                .list(
                    # both below for Google Shared Drives
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    q=query_string,
                    spaces="drive",  # ie not appDataFolder or photos
                    fields="files(id, name)",
                )
                .execute(num_retries=NUM_RETRIES)
            )
            items = results.get("files", [])

            if items:
                ids = ",".join(i["id"] for i in items)
                if len(items) > 1 and use_mime_type:
                    logger.warning(f"{debug_header} found {len(items)} folders with this name, using the last of {ids}")
                elif len(items) > 1:
                    logger.debug(f"{debug_header} found {len(items)} matches, using the last of {ids}")
                return items[-1]["id"]

            if attempt < retries - 1:
                logger.debug(f"{debug_header} not found (attempt {attempt + 1}/{retries}), waiting {sleep_seconds}s")
                time.sleep(sleep_seconds)

        logger.debug(f"{debug_header} not found")
        if raise_on_missing:
            raise ValueError(f"{debug_header} not found after {retries} attempt(s)")
        return None

    def _get_folder_id(self, parent_id: str, name: str, create: bool) -> str:
        """
        Returns the id of folder @name inside @parent_id, from the cache if we've already found or created it.
        If @create, searches once (a new row's folder normally doesn't exist yet, so there's no point waiting for it
        to appear) and creates it if not found. Otherwise searches with the default retries and raises if missing.
        """
        cache_key = (parent_id, name)
        if folder_id := self._folder_ids.get(cache_key):
            return folder_id

        if create:
            folder_id = self._get_id_from_parent_and_name(
                parent_id, name, retries=1, use_mime_type=True, raise_on_missing=False
            )
            if folder_id is None:
                folder_id = self._mkdir(name, parent_id)
            else:
                logger.debug(f"Using existing folder {name} ({folder_id}) in {parent_id}")
        else:
            folder_id = self._get_id_from_parent_and_name(parent_id, name, use_mime_type=True, raise_on_missing=True)

        self._folder_ids[cache_key] = folder_id
        return folder_id

    def _mkdir(self, name: str, parent_id: str):
        """
        Creates a new GDrive folder @name inside folder @parent_id
        Returns id of the created folder
        """
        file_metadata = {"name": [name], "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
        gd_folder = (
            self.service.files()
            .create(supportsAllDrives=True, body=file_metadata, fields="id")
            .execute(num_retries=NUM_RETRIES)
        )
        folder_id = gd_folder.get("id")
        logger.debug(f"Created folder {name} ({folder_id}) in {parent_id}")
        return folder_id
