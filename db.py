"""SQLite layer: sessions, files in sessions, and Drive folder cache.

Concurrency note: SQLite with WAL and short writes is fine for the volume here
(one user, occasional bursts). All access goes through synchronous helpers; FastAPI
runs them in the threadpool so they don't block the event loop.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    sender_phone TEXT NOT NULL,
    plot INTEGER,
    building INTEGER,
    apartment INTEGER,
    stage INTEGER,
    drive_folder_id TEXT,
    drive_folder_path_he TEXT,
    confirm_msg_id TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    bundle_deadline TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_confirm_msg ON sessions(confirm_msg_id);
CREATE INDEX IF NOT EXISTS idx_sessions_chat_sender_status ON sessions(chat_id, sender_phone, status);

CREATE TABLE IF NOT EXISTS session_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    whatsapp_msg_id TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_name TEXT,
    file_size INTEGER,
    mime_type TEXT,
    download_url TEXT NOT NULL,
    uploaded INTEGER NOT NULL DEFAULT 0,
    drive_file_id TEXT,
    drive_file_link TEXT,
    final_filename TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_session ON session_files(session_id);

CREATE TABLE IF NOT EXISTS folder_cache (
    plot INTEGER NOT NULL,
    building INTEGER,
    apartment INTEGER,
    stage_num INTEGER,
    stage_name TEXT,
    drive_folder_id TEXT NOT NULL,
    folder_name TEXT NOT NULL,
    refreshed_at TEXT NOT NULL,
    PRIMARY KEY (plot, building, apartment, stage_num)
);

CREATE TABLE IF NOT EXISTS processed_events (
    event_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);
"""

STATUS_COLLECTING = "collecting"
STATUS_AWAITING_DESTINATION = "awaiting_destination"
STATUS_AWAITING_CONFIRM = "awaiting_confirm"
STATUS_UPLOADING = "uploading"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"
STATUS_ERROR = "error"


@dataclass
class Session:
    id: int
    chat_id: str
    sender_phone: str
    plot: Optional[int]
    building: Optional[int]
    apartment: Optional[int]
    stage: Optional[int]
    drive_folder_id: Optional[str]
    drive_folder_path_he: Optional[str]
    confirm_msg_id: Optional[str]
    status: str
    error_message: Optional[str]
    created_at: str
    bundle_deadline: Optional[str]
    updated_at: str

    @property
    def has_destination(self) -> bool:
        return self.plot is not None and self.stage is not None

    def as_destination(self):
        from parser import Destination
        if not self.has_destination:
            raise ValueError("session has no destination")
        return Destination(self.plot, self.building, self.apartment, self.stage)


@dataclass
class SessionFile:
    id: int
    session_id: int
    whatsapp_msg_id: str
    file_type: str
    file_name: Optional[str]
    file_size: Optional[int]
    mime_type: Optional[str]
    download_url: str
    uploaded: bool
    drive_file_id: Optional[str]
    drive_file_link: Optional[str]
    final_filename: Optional[str]
    error_message: Optional[str]
    created_at: str


_db_path: Path | None = None


def init_db(path: str | Path) -> None:
    """Create tables. Idempotent."""
    global _db_path
    _db_path = Path(path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    if _db_path is None:
        raise RuntimeError("init_db() not called")
    conn = sqlite3.connect(str(_db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(**{k: row[k] for k in row.keys()})


def _row_to_file(row: sqlite3.Row) -> SessionFile:
    return SessionFile(
        id=row["id"],
        session_id=row["session_id"],
        whatsapp_msg_id=row["whatsapp_msg_id"],
        file_type=row["file_type"],
        file_name=row["file_name"],
        file_size=row["file_size"],
        mime_type=row["mime_type"],
        download_url=row["download_url"],
        uploaded=bool(row["uploaded"]),
        drive_file_id=row["drive_file_id"],
        drive_file_link=row["drive_file_link"],
        final_filename=row["final_filename"],
        error_message=row["error_message"],
        created_at=row["created_at"],
    )


# --- sessions ---

def create_session(
    chat_id: str,
    sender_phone: str,
    *,
    status: str,
    plot: Optional[int] = None,
    building: Optional[int] = None,
    apartment: Optional[int] = None,
    stage: Optional[int] = None,
    bundle_deadline: Optional[str] = None,
) -> int:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO sessions
                (chat_id, sender_phone, plot, building, apartment, stage, status,
                 created_at, bundle_deadline, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, sender_phone, plot, building, apartment, stage, status,
             now, bundle_deadline, now),
        )
        return cur.lastrowid


def get_session(session_id: int) -> Optional[Session]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return _row_to_session(row) if row else None


def get_session_by_confirm_msg(confirm_msg_id: str) -> Optional[Session]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE confirm_msg_id = ?",
            (confirm_msg_id,),
        ).fetchone()
        return _row_to_session(row) if row else None


def get_active_session(chat_id: str, sender_phone: str) -> Optional[Session]:
    """Most recent session for this (chat, sender) pair that is still active.

    Group chats may have many people sending in parallel. We index sessions per
    (chat, sender) so each user's bundling and corrections stay isolated from
    everyone else's.
    """
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM sessions
               WHERE chat_id = ? AND sender_phone = ?
                 AND status IN (?, ?, ?)
               ORDER BY id DESC LIMIT 1""",
            (
                chat_id, sender_phone,
                STATUS_COLLECTING,
                STATUS_AWAITING_DESTINATION,
                STATUS_AWAITING_CONFIRM,
            ),
        ).fetchone()
        return _row_to_session(row) if row else None


def update_session(
    session_id: int,
    **fields,
) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [session_id]
    with _connect() as conn:
        conn.execute(f"UPDATE sessions SET {cols} WHERE id = ?", values)


# --- session files ---

def add_file_to_session(
    session_id: int,
    *,
    whatsapp_msg_id: str,
    file_type: str,
    file_name: Optional[str],
    file_size: Optional[int],
    mime_type: Optional[str],
    download_url: str,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO session_files
                (session_id, whatsapp_msg_id, file_type, file_name, file_size,
                 mime_type, download_url, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, whatsapp_msg_id, file_type, file_name, file_size,
             mime_type, download_url, _now()),
        )
        return cur.lastrowid


def list_files(session_id: int) -> list[SessionFile]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM session_files WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [_row_to_file(r) for r in rows]


def mark_file_uploaded(
    file_id: int,
    *,
    drive_file_id: str,
    drive_file_link: str,
    final_filename: str,
) -> None:
    with _connect() as conn:
        conn.execute(
            """UPDATE session_files
               SET uploaded = 1, drive_file_id = ?, drive_file_link = ?,
                   final_filename = ?
               WHERE id = ?""",
            (drive_file_id, drive_file_link, final_filename, file_id),
        )


def mark_file_error(file_id: int, error_message: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE session_files SET error_message = ? WHERE id = ?",
            (error_message, file_id),
        )


# --- folder cache ---

def upsert_folder(
    *,
    plot: int,
    building: Optional[int],
    apartment: Optional[int],
    stage_num: Optional[int],
    stage_name: Optional[str],
    drive_folder_id: str,
    folder_name: str,
) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO folder_cache
                (plot, building, apartment, stage_num, stage_name,
                 drive_folder_id, folder_name, refreshed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (plot, building, apartment, stage_num, stage_name,
             drive_folder_id, folder_name, _now()),
        )


def lookup_folder(
    plot: int,
    building: Optional[int] = None,
    apartment: Optional[int] = None,
    stage_num: Optional[int] = None,
) -> Optional[sqlite3.Row]:
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM folder_cache
               WHERE plot = ?
                 AND building IS ?
                 AND apartment IS ?
                 AND stage_num IS ?""",
            (plot, building, apartment, stage_num),
        ).fetchone()
        return row


def folder_cache_size() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM folder_cache").fetchone()[0]


def list_collecting_sessions_due(now_iso: str) -> list[int]:
    """IDs of sessions in 'collecting' state whose bundle deadline has passed."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id FROM sessions
               WHERE status = ? AND bundle_deadline IS NOT NULL
                 AND bundle_deadline <= ?""",
            (STATUS_COLLECTING, now_iso),
        ).fetchall()
        return [r["id"] for r in rows]


# --- idempotency ---

def is_event_processed(event_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return row is not None


def mark_event_processed(event_id: str) -> None:
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO processed_events (event_id, received_at) VALUES (?, ?)",
                (event_id, _now()),
            )
        except sqlite3.IntegrityError:
            pass
