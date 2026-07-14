from __future__ import annotations

import json
import re
from dataclasses import dataclass

from lark_oapi.api.im.v1 import P2ImMessageReceiveV1


_AT_TAG_RE = re.compile(r"<at[^>]*>.*?</at>", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class FeishuIncomingMessage:
    chat_id: str
    message_id: str
    sender_open_id: str | None
    chat_type: str
    thread_id: str | None
    parent_message_id: str | None
    message_type: str
    text: str
    raw_content: str
    bot_mentioned: bool = False


def _extract_open_id(data: P2ImMessageReceiveV1) -> str | None:
    event = data.event
    if event is None or event.sender is None or event.sender.sender_id is None:
        return None
    sender_id = event.sender.sender_id
    for key in ("open_id", "user_id", "union_id"):
        value = getattr(sender_id, key, None)
        if isinstance(value, str) and value:
            return value
    return None


def _message_mentions_bot(message: object, *, bot_open_id: str | None) -> bool:
    mentions = getattr(message, "mentions", None) or []
    for mention in mentions:
        if getattr(mention, "mentioned_type", None) == "app":
            return True
        user_id = getattr(mention, "id", None)
        if user_id is None or not bot_open_id:
            continue
        for key in ("open_id", "user_id", "union_id"):
            value = getattr(user_id, key, None)
            if isinstance(value, str) and value == bot_open_id:
                return True
    return False


def parse_incoming_message(
    data: P2ImMessageReceiveV1,
    *,
    bot_open_id: str | None = None,
) -> FeishuIncomingMessage | None:
    event = data.event
    if event is None or event.message is None:
        return None
    message = event.message

    sender = event.sender
    if sender is not None and getattr(sender, "sender_type", None) == "app":
        return None

    message_type = message.message_type or ""
    if message_type not in {"text", "post", "file", "image"}:
        return None

    chat_id = message.chat_id
    message_id = message.message_id
    if not chat_id or not message_id:
        return None

    raw_content = message.content or ""
    bot_mentioned = _message_mentions_bot(message, bot_open_id=bot_open_id)
    if not bot_mentioned and "<at " in raw_content.lower():
        bot_mentioned = True
    text = _content_to_text(raw_content, message_type=message_type)
    text = _strip_mentions(text).strip()
    if message_type in {"text", "post"} and not text:
        return None

    thread_id = message.thread_id or message.root_id or message.parent_id
    if thread_id == "":
        thread_id = None
    parent_message_id = message.parent_id or None
    if parent_message_id == "":
        parent_message_id = None

    return FeishuIncomingMessage(
        chat_id=chat_id,
        message_id=message_id,
        sender_open_id=_extract_open_id(data),
        chat_type=message.chat_type or "p2p",
        thread_id=thread_id,
        parent_message_id=parent_message_id,
        message_type=message_type,
        text=text,
        raw_content=raw_content,
        bot_mentioned=bot_mentioned,
    )


def _content_to_text(raw_content: str, *, message_type: str) -> str:
    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError:
        return raw_content

    if message_type == "text":
        text = payload.get("text")
        return str(text) if isinstance(text, str) else raw_content

    if message_type == "post":
        title = payload.get("title")
        parts: list[str] = []
        if isinstance(title, str) and title.strip():
            parts.append(title.strip())
        content = payload.get("content")
        if isinstance(content, list):
            for row in content:
                if not isinstance(row, list):
                    continue
                for cell in row:
                    if not isinstance(cell, dict):
                        continue
                    tag = cell.get("tag")
                    if tag == "text":
                        value = cell.get("text")
                        if isinstance(value, str) and value.strip():
                            parts.append(value.strip())
        return "\n".join(parts)

    return raw_content


def _strip_mentions(text: str) -> str:
    cleaned = _AT_TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", cleaned).strip()
