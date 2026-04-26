"""Green API client: parse incoming webhooks + send replies.

Webhook event reference (Green API v1):
  - incomingMessageReceived: media (image/video) and text messages
    nested under messageData.{typeMessage}.{fileMessageData|textMessageData}
  - reactionMessage (also delivered as incomingMessageReceived with type=reactionMessage):
    references the messageId being reacted to
  - outgoingMessageStatus, deviceInfo, etc — ignored

Docs that matter:
  https://green-api.com/en/docs/api/receiving/notifications-format/incoming-message/
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

LIKE_EMOJIS = {"\U0001F44D", "👍", "+1"}  # codepoints + literal


# --- inbound: parsed webhook events ---

@dataclass
class IncomingMedia:
    chat_id: str
    sender: str  # phone number without @c.us
    msg_id: str
    file_type: str  # "video" | "image"
    file_name: Optional[str]
    file_size: Optional[int]
    mime_type: Optional[str]
    download_url: str
    caption: Optional[str]
    timestamp: int


@dataclass
class IncomingText:
    chat_id: str
    sender: str
    msg_id: str
    text: str
    timestamp: int


@dataclass
class IncomingReaction:
    chat_id: str
    sender: str
    msg_id: str  # the reaction message itself
    target_msg_id: str  # the message being reacted to
    emoji: str
    timestamp: int

    @property
    def is_like(self) -> bool:
        return self.emoji in LIKE_EMOJIS or "👍" in self.emoji


def parse_webhook(payload: dict) -> Optional[IncomingMedia | IncomingText | IncomingReaction]:
    """Return a parsed event or None if the payload is not actionable."""
    if payload.get("typeWebhook") != "incomingMessageReceived":
        return None

    sender_data = payload.get("senderData") or {}
    chat_id = sender_data.get("chatId") or ""
    sender = (sender_data.get("sender") or "").split("@")[0]
    msg_id = payload.get("idMessage") or ""
    timestamp = int(payload.get("timestamp") or 0)

    msg_data = payload.get("messageData") or {}
    type_msg = msg_data.get("typeMessage")

    if type_msg in ("imageMessage", "videoMessage"):
        fm = msg_data.get("fileMessageData") or {}
        return IncomingMedia(
            chat_id=chat_id,
            sender=sender,
            msg_id=msg_id,
            file_type="video" if type_msg == "videoMessage" else "image",
            file_name=fm.get("fileName"),
            file_size=_safe_int(fm.get("fileSize")),
            mime_type=fm.get("mimeType"),
            download_url=fm.get("downloadUrl") or "",
            caption=fm.get("caption"),
            timestamp=timestamp,
        )

    if type_msg in ("textMessage", "extendedTextMessage"):
        tm = msg_data.get("textMessageData") or msg_data.get("extendedTextMessageData") or {}
        text = tm.get("textMessage") or tm.get("text") or ""
        return IncomingText(
            chat_id=chat_id, sender=sender, msg_id=msg_id, text=text, timestamp=timestamp,
        )

    if type_msg == "reactionMessage":
        # Real Green API shape (verified 2026-04 against live webhook):
        #   messageData.extendedTextMessageData.text  -> the emoji
        #   messageData.quotedMessage.stanzaId        -> the message being reacted to
        rm = msg_data.get("extendedTextMessageData") or msg_data.get("reactionMessageData") or {}
        quoted = msg_data.get("quotedMessage") or {}
        return IncomingReaction(
            chat_id=chat_id,
            sender=sender,
            msg_id=msg_id,
            target_msg_id=(
                quoted.get("stanzaId")
                or rm.get("stanzaId")
                or rm.get("targetMessageId")
                or ""
            ),
            emoji=rm.get("text") or rm.get("emoji") or "",
            timestamp=timestamp,
        )

    return None


def _safe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# --- outbound: send messages ---

class GreenAPIClient:
    def __init__(self, base_url: str, instance_id: str, token: str):
        self.base = base_url.rstrip("/")
        self.instance = instance_id
        self.token = token
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send_message(
        self,
        chat_id: str,
        message: str,
        *,
        quoted_msg_id: Optional[str] = None,
    ) -> str:
        """Send a text message. When ``quoted_msg_id`` is given, it appears as a
        reply to that message (so the user sees which media it concerns).

        Returns the WhatsApp message id of the sent message, used for reaction
        tracking on the confirmation message.
        """
        url = f"{self.base}/waInstance{self.instance}/sendMessage/{self.token}"
        body: dict = {"chatId": chat_id, "message": message}
        if quoted_msg_id:
            body["quotedMessageId"] = quoted_msg_id
        resp = await self._client.post(url, json=body)
        resp.raise_for_status()
        return resp.json().get("idMessage") or ""
