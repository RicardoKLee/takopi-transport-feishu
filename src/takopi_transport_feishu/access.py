from __future__ import annotations

from .settings import FeishuTransportSettings


def is_chat_allowed(settings: FeishuTransportSettings, chat_id: str) -> bool:
    if not settings.allowed_chat_ids:
        return True
    return chat_id in settings.allowed_chat_ids


def is_sender_allowed(settings: FeishuTransportSettings, open_id: str | None) -> bool:
    if not settings.allowed_open_ids:
        return True
    if not open_id:
        return False
    return open_id in settings.allowed_open_ids


def should_handle_group_message(
    settings: FeishuTransportSettings,
    *,
    chat_type: str,
    text: str,
    bot_name: str | None,
    bot_mentioned: bool = False,
) -> bool:
    if chat_type != "group":
        return True
    if not settings.require_mention_in_group:
        return True
    if bot_mentioned:
        return True
    lowered = text.lower()
    if "@_all" in lowered or "@all" in lowered:
        return False
    if bot_name and f"@{bot_name.lower()}" in lowered:
        return True
    return "@_user" in text or "<at " in lowered
