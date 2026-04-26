"""Google Drive client.

Two responsibilities:
  1. Resolve a Destination (plot/building/apt/stage) to a Drive folder ID,
     caching the entire tree in SQLite to avoid hammering the Drive API.
  2. Stream-upload a file from a remote HTTP URL into that folder, never
     touching local disk (the bytes flow through memory in chunks).

Folder naming conventions in Drive (must match the local tree):
  level 1: "מגרש 1096"
  level 2: "בניין 6"
  level 3: "דירה 1"
  level 4: "01 - ניקיון לפני בנאים", ..., "44 - מסירה סופית"

Stage matching: numeric prefix can be "01" or "1"; we accept both.
"""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Iterator, Optional

import httpx
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

import db
from parser import Destination

log = logging.getLogger(__name__)

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]
FOLDER_MIME = "application/vnd.google-apps.folder"
UPLOAD_CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB

PLOT_PREFIX = "מגרש "
BUILDING_PREFIX = "בניין "
APT_PREFIX = "דירה "
STAGE_PATTERN = re.compile(r"^\s*(\d{1,3})\s*-\s*(.+)\s*$")


class DriveError(Exception):
    pass


class FolderNotFound(DriveError):
    """A specific level of the path doesn't exist in Drive."""

    def __init__(self, level: str, value: str | int):
        self.level = level
        self.value = value
        super().__init__(f"{level} {value} לא נמצא במערכת")


@dataclass
class ResolvedDestination:
    folder_id: str
    folder_path_he: str  # "מגרש 1096 / בניין 6 / דירה 1 / 18 - טיח פנים"
    stage_name: str  # "טיח פנים"


class DriveClient:
    def __init__(self, credentials_info: dict, root_folder_id: str):
        creds = _build_credentials(credentials_info)
        self._svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.root_folder_id = root_folder_id

    # ---- folder resolution ----

    def resolve(self, dest: Destination) -> ResolvedDestination:
        """Walk plot → building → apartment → stage, using cache + Drive lookups."""
        plot_id, plot_name = self._find_or_lookup(
            parent_id=self.root_folder_id,
            cache_key=("plot", dest.plot, None, None, None),
            label_prefix=PLOT_PREFIX,
            label_value=str(dest.plot),
            level_label="מגרש",
        )
        building_id, building_name = self._find_or_lookup(
            parent_id=plot_id,
            cache_key=("building", dest.plot, dest.building, None, None),
            label_prefix=BUILDING_PREFIX,
            label_value=str(dest.building),
            level_label="בניין",
        )
        apt_id, apt_name = self._find_or_lookup(
            parent_id=building_id,
            cache_key=("apt", dest.plot, dest.building, dest.apartment, None),
            label_prefix=APT_PREFIX,
            label_value=str(dest.apartment),
            level_label="דירה",
        )
        stage_id, stage_full = self._find_stage(
            parent_id=apt_id, dest=dest,
        )
        stage_name = self._extract_stage_name(stage_full)

        return ResolvedDestination(
            folder_id=stage_id,
            folder_path_he=f"{plot_name} / {building_name} / {apt_name} / {stage_full}",
            stage_name=stage_name,
        )

    def _find_or_lookup(
        self,
        *,
        parent_id: str,
        cache_key: tuple,
        label_prefix: str,
        label_value: str,
        level_label: str,
    ) -> tuple[str, str]:
        kind, plot, building, apt, stage_num = cache_key
        cached = db.lookup_folder(plot=plot, building=building, apartment=apt, stage_num=stage_num)
        if cached:
            return cached["drive_folder_id"], cached["folder_name"]

        target_name = f"{label_prefix}{label_value}"
        folder = self._find_child_folder(parent_id, target_name)
        if not folder:
            raise FolderNotFound(level_label, label_value)

        db.upsert_folder(
            plot=plot, building=building, apartment=apt, stage_num=stage_num,
            stage_name=None,
            drive_folder_id=folder["id"],
            folder_name=folder["name"],
        )
        return folder["id"], folder["name"]

    def _find_stage(self, *, parent_id: str, dest: Destination) -> tuple[str, str]:
        cached = db.lookup_folder(
            plot=dest.plot, building=dest.building, apartment=dest.apartment, stage_num=dest.stage,
        )
        if cached:
            return cached["drive_folder_id"], cached["folder_name"]

        # List all children once and look for "{stage:02d} - ..." OR "{stage} - ..."
        children = self._list_subfolders(parent_id)
        match = self._match_stage(children, dest.stage)
        if not match:
            raise FolderNotFound("שלב", dest.stage)

        db.upsert_folder(
            plot=dest.plot, building=dest.building, apartment=dest.apartment, stage_num=dest.stage,
            stage_name=self._extract_stage_name(match["name"]),
            drive_folder_id=match["id"],
            folder_name=match["name"],
        )
        return match["id"], match["name"]

    @staticmethod
    def _match_stage(children: list[dict], stage_num: int) -> Optional[dict]:
        for c in children:
            m = STAGE_PATTERN.match(c["name"])
            if m and int(m.group(1)) == stage_num:
                return c
        return None

    @staticmethod
    def _extract_stage_name(folder_name: str) -> str:
        m = STAGE_PATTERN.match(folder_name)
        return m.group(2).strip() if m else folder_name

    # ---- Drive API primitives ----

    def _find_child_folder(self, parent_id: str, name: str) -> Optional[dict]:
        # Drive query: name + mime type + parent. Note: name match is case-sensitive
        # exact, so we query and let Drive return matches.
        safe_name = name.replace("'", "\\'")
        q = (
            f"'{parent_id}' in parents and "
            f"mimeType = '{FOLDER_MIME}' and "
            f"name = '{safe_name}' and trashed = false"
        )
        resp = self._svc.files().list(
            q=q, fields="files(id,name)", pageSize=10,
        ).execute()
        files = resp.get("files", [])
        return files[0] if files else None

    def _list_subfolders(self, parent_id: str) -> list[dict]:
        q = (
            f"'{parent_id}' in parents and "
            f"mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        out: list[dict] = []
        page_token = None
        while True:
            resp = self._svc.files().list(
                q=q,
                fields="nextPageToken, files(id,name)",
                pageSize=200,
                pageToken=page_token,
            ).execute()
            out.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return out

    def list_filenames_in(self, folder_id: str) -> set[str]:
        """Names of all (non-trashed) files in a folder. Used for filename dedup."""
        q = f"'{folder_id}' in parents and trashed = false"
        out: set[str] = set()
        page_token = None
        while True:
            resp = self._svc.files().list(
                q=q,
                fields="nextPageToken, files(name)",
                pageSize=200,
                pageToken=page_token,
            ).execute()
            for f in resp.get("files", []):
                out.add(f["name"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return out

    # ---- upload ----

    def stream_upload(
        self,
        *,
        download_url: str,
        folder_id: str,
        target_filename: str,
        mime_type: str,
        size_hint: Optional[int] = None,
    ) -> dict:
        """Download bytes into an in-memory buffer and resumable-upload to Drive.

        Why not pure streaming? ``MediaIoBaseUpload`` requires a seekable stream
        (it calls ``seek(0, SEEK_END)`` to learn the size) and Green API webhooks
        don't include ``fileSize``, so we can't drive an unknown-length resumable
        upload from the SDK. Buffering once in memory is acceptable for WhatsApp
        media (16MB cap for chat videos). For >200MB files we'd need a hand-rolled
        resumable-upload loop against the Drive REST API.

        Bytes never touch the local filesystem.
        """
        with httpx.stream(
            "GET", download_url,
            timeout=httpx.Timeout(connect=15, read=300, write=300, pool=15),
        ) as resp:
            resp.raise_for_status()
            buf = io.BytesIO()
            for chunk in resp.iter_bytes(UPLOAD_CHUNK_SIZE):
                buf.write(chunk)
            buf.seek(0)

        media = MediaIoBaseUpload(
            buf,
            mimetype=mime_type or "application/octet-stream",
            chunksize=UPLOAD_CHUNK_SIZE,
            resumable=True,
        )
        metadata = {"name": target_filename, "parents": [folder_id]}
        request = self._svc.files().create(
            body=metadata,
            media_body=media,
            fields="id,webViewLink",
            supportsAllDrives=True,
        )
        response = None
        while response is None:
            _status, response = request.next_chunk(num_retries=3)
        return response


class _IterBytesIO(io.RawIOBase):
    """Wrap an httpx ``iter_bytes`` generator as a file-like object.

    `MediaIoBaseUpload` calls ``.read(chunksize)`` repeatedly; we serve those
    reads from the underlying iterator, buffering only one chunk at a time.
    """

    def __init__(self, iterator: Iterator[bytes], size_hint: Optional[int] = None):
        self._it = iterator
        self._buf = b""
        self._eof = False
        self._size_hint = size_hint

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunks = [self._buf]
            self._buf = b""
            for chunk in self._it:
                chunks.append(chunk)
            self._eof = True
            return b"".join(chunks)

        while len(self._buf) < size and not self._eof:
            try:
                self._buf += next(self._it)
            except StopIteration:
                self._eof = True
                break

        out, self._buf = self._buf[:size], self._buf[size:]
        return out


def load_credentials_from_env(value: str) -> dict:
    """Accept either a JSON string (Render env var) or a path to a JSON file."""
    value = value.strip()
    if value.startswith("{"):
        return json.loads(value)
    with open(value, "r") as f:
        return json.load(f)


def _build_credentials(info: dict):
    """Accept either a service-account JSON or an OAuth user-credentials dict.

    Service account dict has ``type=='service_account'``.
    OAuth user dict has ``refresh_token`` (and ``client_id``, ``client_secret``).
    """
    if info.get("type") == "service_account":
        return service_account.Credentials.from_service_account_info(
            info, scopes=DRIVE_SCOPES
        )
    if "refresh_token" in info:
        return UserCredentials(
            token=None,
            refresh_token=info["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=info["client_id"],
            client_secret=info["client_secret"],
            scopes=DRIVE_SCOPES,
        )
    raise ValueError(
        "credentials format not recognised — need 'service_account' JSON "
        "or an OAuth user dict with refresh_token+client_id+client_secret"
    )
