"""FastAPI entry point — webhook + health endpoints + background poller."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, Request

import db
from drive_client import DRIVE_SCOPES, DriveClient, _build_credentials, load_credentials_from_env
from greenapi_client import (
    GreenAPIClient, IncomingMedia, IncomingReaction, IncomingText, parse_webhook,
)
from session_manager import SessionManager
from sheets_client import SheetsClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("media-bot")

load_dotenv()

# --- env ---
GREEN_API_URL = os.environ["GREEN_API_URL"]
GREEN_API_INSTANCE = os.environ["GREEN_API_INSTANCE"]
GREEN_API_TOKEN = os.environ["GREEN_API_TOKEN"]
AUTHORIZED_GROUP_ID = os.environ["AUTHORIZED_GROUP_ID"]  # e.g. "120363...@g.us"
DRIVE_ROOT_FOLDER_ID = os.environ["DRIVE_ROOT_FOLDER_ID"]
GANTT_SHEET_ID = os.environ.get("GANTT_SHEET_ID", "")  # optional: skip sheet write if empty
DB_PATH = os.environ.get("DB_PATH", "./data/bot.db")
BUNDLE_WINDOW_SECONDS = int(os.environ.get("BUNDLE_WINDOW_SECONDS", "10"))

# Google credentials: inline JSON (cloud) or a file path (local).
# Accepts either a service-account dict or an OAuth user dict from bootstrap_oauth.py.
_creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
_creds_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "./credentials.json")
GOOGLE_CREDS = load_credentials_from_env(_creds_json or _creds_path)

# --- singletons (assembled in lifespan) ---
green: GreenAPIClient | None = None
drive: DriveClient | None = None
sheets: SheetsClient | None = None
manager: SessionManager | None = None
poller_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global green, drive, manager, poller_task

    db.init_db(DB_PATH)
    green = GreenAPIClient(GREEN_API_URL, GREEN_API_INSTANCE, GREEN_API_TOKEN)
    drive = DriveClient(GOOGLE_CREDS, DRIVE_ROOT_FOLDER_ID)
    sheets = (
        SheetsClient(_build_credentials(GOOGLE_CREDS), GANTT_SHEET_ID)
        if GANTT_SHEET_ID else None
    )
    manager = SessionManager(green, drive, sheets, bundle_window_seconds=BUNDLE_WINDOW_SECONDS)

    log.info("startup ok | bundle_window=%ss | db=%s", BUNDLE_WINDOW_SECONDS, DB_PATH)
    poller_task = asyncio.create_task(_run_poller(manager))
    try:
        yield
    finally:
        if poller_task:
            poller_task.cancel()
            try:
                await poller_task
            except asyncio.CancelledError:
                pass
        if green:
            await green.aclose()


app = FastAPI(lifespan=lifespan)


async def _run_poller(mgr: SessionManager) -> None:
    while True:
        try:
            await mgr.process_due_sessions()
        except Exception:
            log.exception("poller iteration failed")
        await asyncio.sleep(1.0)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.post("/webhook")
async def webhook(request: Request) -> dict:
    payload = await request.json()
    event_id = payload.get("idMessage") or payload.get("receiptId")

    # Idempotency: Green API may retry. Skip if we already handled this id.
    if event_id and db.is_event_processed(str(event_id)):
        return {"ok": True, "duplicate": True}

    try:
        event = parse_webhook(payload)
    except Exception:
        log.exception("parse_webhook crashed | payload=%s", payload)
        return {"ok": True, "ignored": "parse_error"}

    if event is None:
        if event_id:
            db.mark_event_processed(str(event_id))
        return {"ok": True, "ignored": "non-actionable"}

    # Whitelist guard: silently drop anything that isn't from the authorized group.
    if event.chat_id != AUTHORIZED_GROUP_ID:
        log.warning(
            "rejected non-authorized chat=%s sender=%s msgId=%s",
            event.chat_id, event.sender, event.msg_id,
        )
        if event_id:
            db.mark_event_processed(str(event_id))
        return {"ok": True, "ignored": "unauthorized"}

    assert manager is not None
    try:
        if isinstance(event, IncomingMedia):
            await manager.on_media(event)
        elif isinstance(event, IncomingText):
            await manager.on_text(event)
        elif isinstance(event, IncomingReaction):
            await manager.on_reaction(event)
    except Exception as exc:
        log.exception("handler crashed | event=%s", event)
        # Don't go silent — tell the user the action failed so they don't sit waiting.
        try:
            chat = getattr(event, "chat_id", None)
            msg_id = getattr(event, "msg_id", None)
            if chat and green is not None:
                await green.send_message(
                    chat,
                    f"⚠️ משהו השתבש בעיבוד ההודעה ({type(exc).__name__}). נסה שוב, או פנה למנהל אם זה חוזר.",
                    quoted_msg_id=msg_id,
                )
        except Exception:
            log.exception("failed to send user-facing error notice")
        return {"ok": True, "ignored": "handler_error"}

    if event_id:
        db.mark_event_processed(str(event_id))
    return {"ok": True}
