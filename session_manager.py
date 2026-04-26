"""Session orchestration: from incoming events to confirmations and uploads.

State machine per session (see ``db.STATUS_*``):

  collecting ──[10s timer expires]──> awaiting_confirm
       │                                    │
       │ on more naked media (resets timer) │ on 👍 reaction
       │                                    ↓
       │                              uploading ──> completed / error
       │                                    ↑
       │                                    │ on text=destination (correction)
       │                                    │ → re-enters with new destination
       ↓
  awaiting_destination (used when first file had no caption)
       └── on text=destination → collecting (with 10s timer)

The latest awaiting_confirm session for a chat is the implicit target of
text replies that look like a destination (corrections).

Concurrency: webhook events arrive over HTTP and are dispatched here in the
order of arrival. The 10s window is enforced by ``process_due_sessions``,
called by a background poller in ``main.py`` once per second.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import db
import messages
from drive_client import DriveClient, FolderNotFound
from filename import build_filename
from greenapi_client import (
    GreenAPIClient, IncomingMedia, IncomingReaction, IncomingText,
)
from parser import Destination, ParseError, parse_caption
from sheets_client import SheetsClient

log = logging.getLogger(__name__)


class SessionManager:
    def __init__(
        self,
        green: GreenAPIClient,
        drive: DriveClient,
        sheets: Optional[SheetsClient],
        bundle_window_seconds: int = 10,
    ):
        self.green = green
        self.drive = drive
        self.sheets = sheets
        self.window = bundle_window_seconds

    # ---- entry points ----

    async def on_media(self, media: IncomingMedia) -> None:
        try:
            dest = parse_caption(media.caption)
        except ParseError:
            await self.green.send_message(
                media.chat_id, messages.parse_error(), quoted_msg_id=media.msg_id,
            )
            return

        if dest is not None:
            await self._open_session_with_destination(media, dest)
            return

        # No caption. Try to attach to most recent active session for THIS sender.
        active = db.get_active_session(media.chat_id, media.sender)
        if active and active.status == db.STATUS_COLLECTING and active.has_destination:
            self._add_file(active.id, media)
            self._extend_deadline(active.id)
            return

        if active and active.status == db.STATUS_AWAITING_DESTINATION:
            self._add_file(active.id, media)
            return  # still waiting for the user to type a destination

        # Otherwise: brand-new naked-file session, ask for destination.
        sid = db.create_session(
            media.chat_id, media.sender, status=db.STATUS_AWAITING_DESTINATION,
        )
        self._add_file(sid, media)
        await self.green.send_message(
            media.chat_id, messages.need_destination(), quoted_msg_id=media.msg_id,
        )

    async def on_text(self, text: IncomingText) -> None:
        body = (text.text or "").strip()
        if not body:
            return

        # 'לא' on the latest awaiting_confirm session = cancel
        active = db.get_active_session(text.chat_id, text.sender)
        active_quote = self._first_media_msg_id(active.id) if active else None
        if body in {"לא", "ביטול", "no", "cancel"}:
            if active:
                db.update_session(active.id, status=db.STATUS_CANCELLED)
                await self.green.send_message(
                    text.chat_id, messages.cancelled(), quoted_msg_id=active_quote,
                )
            return

        try:
            dest = parse_caption(body)
        except ParseError:
            if active:
                await self.green.send_message(
                    text.chat_id, messages.parse_error(), quoted_msg_id=active_quote,
                )
            return

        if dest is None:
            # text isn't a destination at all
            if active:
                await self.green.send_message(
                    text.chat_id, messages.no_active_session(), quoted_msg_id=active_quote,
                )
            return

        # Text IS a valid destination. Behavior depends on active session state.
        if active is None:
            # No pending session, no files. Ignore — user must send media first.
            await self.green.send_message(text.chat_id, messages.no_active_session())
            return

        if active.status == db.STATUS_AWAITING_DESTINATION:
            # Attach destination to the waiting files, start the 10s window.
            await self._attach_destination(active.id, text.chat_id, dest)
            return

        if active.status == db.STATUS_AWAITING_CONFIRM:
            # Correction.
            await self._attach_destination(active.id, text.chat_id, dest, is_correction=True)
            return

        if active.status == db.STATUS_COLLECTING:
            # Same destination → no-op. Different destination → branch off:
            # mark current session ready (its files keep their destination), let
            # the timer fire normally. The user must send NEW files to the new
            # destination — text alone doesn't open an empty session.
            if active.has_destination and active.as_destination() == dest:
                return
            await self.green.send_message(
                text.chat_id,
                "🤔 קיבלתי ניתוב חדש אבל אין קבצים מצורפים אליו. שלח קובץ עם הניתוב.",
                quoted_msg_id=active_quote,
            )
            return

    async def on_reaction(self, react: IncomingReaction) -> None:
        if not react.is_like:
            return
        session = db.get_session_by_confirm_msg(react.target_msg_id)
        if not session or session.status != db.STATUS_AWAITING_CONFIRM:
            return
        # Only the original sender can confirm their own upload (gisha א).
        # Anyone else's 👍 is treated as a regular reaction in the chat.
        if react.sender != session.sender_phone:
            return
        await self._upload_session(session.id)

    # ---- background poller ----

    async def process_due_sessions(self) -> None:
        """Send confirmations for any 'collecting' session past its deadline."""
        now_iso = datetime.utcnow().isoformat(timespec="seconds")
        for sid in db.list_collecting_sessions_due(now_iso):
            try:
                await self._send_confirmation(sid)
            except Exception:
                log.exception("failed to send confirmation for session %s", sid)

    # ---- internals ----

    async def _open_session_with_destination(
        self, media: IncomingMedia, dest: Destination,
    ) -> None:
        # If the latest collecting session for this sender has the same destination, append.
        active = db.get_active_session(media.chat_id, media.sender)
        if (
            active
            and active.status == db.STATUS_COLLECTING
            and active.has_destination
            and active.as_destination() == dest
        ):
            self._add_file(active.id, media)
            self._extend_deadline(active.id)
            return

        sid = db.create_session(
            media.chat_id,
            media.sender,
            status=db.STATUS_COLLECTING,
            plot=dest.plot,
            building=dest.building,
            apartment=dest.apartment,
            stage=dest.stage,
            bundle_deadline=self._deadline_iso(),
        )
        self._add_file(sid, media)

    async def _attach_destination(
        self, session_id: int, chat_id: str, dest: Destination, *, is_correction: bool = False,
    ) -> None:
        quote = self._first_media_msg_id(session_id)
        try:
            resolved = self.drive.resolve(dest)
        except FolderNotFound as e:
            await self.green.send_message(
                chat_id, messages.folder_not_found(e.level, e.value), quoted_msg_id=quote,
            )
            return

        db.update_session(
            session_id,
            plot=dest.plot, building=dest.building,
            apartment=dest.apartment, stage=dest.stage,
            drive_folder_id=resolved.folder_id,
            drive_folder_path_he=resolved.folder_path_he,
            status=db.STATUS_COLLECTING if not is_correction else db.STATUS_AWAITING_CONFIRM,
            bundle_deadline=self._deadline_iso() if not is_correction else None,
        )

        if is_correction:
            # Re-send confirmation immediately with the new destination.
            await self._send_confirmation(session_id, force=True)

    async def _send_confirmation(self, session_id: int, *, force: bool = False) -> None:
        session = db.get_session(session_id)
        if not session:
            return
        if not force and session.status != db.STATUS_COLLECTING:
            return
        files = db.list_files(session_id)
        quote = files[0].whatsapp_msg_id if files else None
        if not session.has_destination or not session.drive_folder_path_he:
            # Resolve now if not yet resolved (e.g., destination was set but resolve wasn't called)
            try:
                resolved = self.drive.resolve(session.as_destination())
                db.update_session(
                    session_id,
                    drive_folder_id=resolved.folder_id,
                    drive_folder_path_he=resolved.folder_path_he,
                )
                session = db.get_session(session_id)
            except FolderNotFound as e:
                await self.green.send_message(
                    session.chat_id, messages.folder_not_found(e.level, e.value),
                    quoted_msg_id=quote,
                )
                db.update_session(session_id, status=db.STATUS_ERROR, error_message=str(e))
                return

        text = messages.confirmation(session.drive_folder_path_he, [{} for _ in files])
        confirm_msg_id = await self.green.send_message(session.chat_id, text, quoted_msg_id=quote)
        db.update_session(
            session_id,
            status=db.STATUS_AWAITING_CONFIRM,
            confirm_msg_id=confirm_msg_id,
            bundle_deadline=None,
        )

    async def _upload_session(self, session_id: int) -> None:
        session = db.get_session(session_id)
        if not session:
            return
        db.update_session(session_id, status=db.STATUS_UPLOADING)

        files = db.list_files(session_id)
        quote = files[0].whatsapp_msg_id if files else None
        await self.green.send_message(
            session.chat_id, messages.upload_started(len(files)), quoted_msg_id=quote,
        )

        existing = self.drive.list_filenames_in(session.drive_folder_id)
        ok = 0
        errors: list[str] = []
        first_link: Optional[str] = None
        today = date.today()

        for f in files:
            if f.uploaded:
                ok += 1
                continue
            target = build_filename(
                dest=session.as_destination(),
                on_date=today,
                original_name=f.file_name,
                existing_names=existing,
            )
            try:
                result = await asyncio.to_thread(
                    self.drive.stream_upload,
                    download_url=f.download_url,
                    folder_id=session.drive_folder_id,
                    target_filename=target,
                    mime_type=f.mime_type or "application/octet-stream",
                    size_hint=f.file_size,
                )
            except Exception as e:
                log.exception("upload failed for file %s", f.id)
                db.mark_file_error(f.id, str(e))
                errors.append(f"{f.file_name}: {e}")
                continue

            db.mark_file_uploaded(
                f.id,
                drive_file_id=result["id"],
                drive_file_link=result.get("webViewLink", ""),
                final_filename=target,
            )
            existing.add(target)
            ok += 1
            if first_link is None:
                first_link = result.get("webViewLink")

        if ok == len(files) and not errors:
            db.update_session(session_id, status=db.STATUS_COMPLETED)
            if self.sheets:
                folder_url = f"https://drive.google.com/drive/folders/{session.drive_folder_id}"
                try:
                    await asyncio.to_thread(
                        self.sheets.set_documentation_link,
                        plot=session.plot, building=session.building,
                        apartment=session.apartment, stage=session.stage,
                        url=folder_url,
                    )
                except Exception:
                    log.exception("sheet update failed for session %s — upload still succeeded", session_id)
            if len(files) == 1 and first_link:
                msg = messages.upload_done_single(session.drive_folder_path_he, first_link)
            else:
                msg = messages.upload_done_bundle(session.drive_folder_path_he, ok)
        else:
            db.update_session(session_id, status=db.STATUS_ERROR, error_message="; ".join(errors))
            msg = messages.upload_partial_failure(session.drive_folder_path_he, ok, len(files), errors)
        await self.green.send_message(session.chat_id, msg, quoted_msg_id=quote)

    def _first_media_msg_id(self, session_id: int) -> Optional[str]:
        """The whatsapp_msg_id of the first media in the session, used as a quote
        target so the bot's replies appear attached to that media in the chat."""
        files = db.list_files(session_id)
        return files[0].whatsapp_msg_id if files else None

    def _add_file(self, session_id: int, media: IncomingMedia) -> None:
        db.add_file_to_session(
            session_id,
            whatsapp_msg_id=media.msg_id,
            file_type=media.file_type,
            file_name=media.file_name,
            file_size=media.file_size,
            mime_type=media.mime_type,
            download_url=media.download_url,
        )

    def _extend_deadline(self, session_id: int) -> None:
        db.update_session(session_id, bundle_deadline=self._deadline_iso())

    def _deadline_iso(self) -> str:
        return (datetime.utcnow() + timedelta(seconds=self.window)).isoformat(timespec="seconds")
