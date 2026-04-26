"""Webhook payload parsing — covers shapes Green API actually delivers."""
from greenapi_client import (
    IncomingMedia, IncomingReaction, IncomingText, parse_webhook,
)


def test_video_with_caption():
    payload = {
        "typeWebhook": "incomingMessageReceived",
        "idMessage": "MSG_VID_1",
        "timestamp": 1700000000,
        "senderData": {
            "chatId": "972529526517@c.us",
            "sender": "972529526517@c.us",
            "senderName": "זלמי",
        },
        "messageData": {
            "typeMessage": "videoMessage",
            "fileMessageData": {
                "downloadUrl": "https://example.com/v.mp4",
                "caption": "1096/6/1/18",
                "fileName": "v.mp4",
                "mimeType": "video/mp4",
                "fileSize": "12345678",
            },
        },
    }
    ev = parse_webhook(payload)
    assert isinstance(ev, IncomingMedia)
    assert ev.file_type == "video"
    assert ev.caption == "1096/6/1/18"
    assert ev.file_size == 12345678
    assert ev.sender == "972529526517"


def test_image_no_caption():
    payload = {
        "typeWebhook": "incomingMessageReceived",
        "idMessage": "MSG_IMG_1",
        "timestamp": 1700000000,
        "senderData": {"chatId": "972529526517@c.us", "sender": "972529526517@c.us"},
        "messageData": {
            "typeMessage": "imageMessage",
            "fileMessageData": {
                "downloadUrl": "https://example.com/i.jpg",
                "fileName": "i.jpg",
                "mimeType": "image/jpeg",
                "fileSize": 9999,
            },
        },
    }
    ev = parse_webhook(payload)
    assert isinstance(ev, IncomingMedia)
    assert ev.file_type == "image"
    assert ev.caption is None


def test_text_message():
    payload = {
        "typeWebhook": "incomingMessageReceived",
        "idMessage": "MSG_TXT_1",
        "timestamp": 1700000000,
        "senderData": {"chatId": "972529526517@c.us", "sender": "972529526517@c.us"},
        "messageData": {
            "typeMessage": "textMessage",
            "textMessageData": {"textMessage": "1096/6/2/19"},
        },
    }
    ev = parse_webhook(payload)
    assert isinstance(ev, IncomingText)
    assert ev.text == "1096/6/2/19"


def test_reaction_like_real_shape():
    """Real Green API reaction payload — stanzaId lives under quotedMessage."""
    payload = {
        "typeWebhook": "incomingMessageReceived",
        "idMessage": "MSG_REACT_1",
        "timestamp": 1700000000,
        "senderData": {"chatId": "972529526517@c.us", "sender": "972529526517@c.us"},
        "messageData": {
            "typeMessage": "reactionMessage",
            "extendedTextMessageData": {"text": "👍"},
            "quotedMessage": {
                "stanzaId": "BOT_CONFIRM_MSG_1",
                "participant": "972529526517@c.us",
            },
        },
    }
    ev = parse_webhook(payload)
    assert isinstance(ev, IncomingReaction)
    assert ev.target_msg_id == "BOT_CONFIRM_MSG_1"
    assert ev.is_like


def test_reaction_other_emoji_not_like():
    payload = {
        "typeWebhook": "incomingMessageReceived",
        "idMessage": "MSG_REACT_2",
        "timestamp": 1700000000,
        "senderData": {"chatId": "972529526517@c.us", "sender": "972529526517@c.us"},
        "messageData": {
            "typeMessage": "reactionMessage",
            "extendedTextMessageData": {"text": "❤️", "stanzaId": "X"},
        },
    }
    ev = parse_webhook(payload)
    assert isinstance(ev, IncomingReaction)
    assert not ev.is_like


def test_outgoing_status_ignored():
    payload = {"typeWebhook": "outgoingMessageStatus"}
    assert parse_webhook(payload) is None


def test_unknown_type_ignored():
    payload = {
        "typeWebhook": "incomingMessageReceived",
        "messageData": {"typeMessage": "audioMessage"},
        "senderData": {},
    }
    assert parse_webhook(payload) is None
