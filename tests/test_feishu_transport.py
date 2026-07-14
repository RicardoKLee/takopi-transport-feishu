from __future__ import annotations

import json

import pytest

from takopi_transport_feishu.access import (
    is_chat_allowed,
    is_sender_allowed,
    should_handle_group_message,
)
from takopi_transport_feishu.messages import _content_to_text, _strip_mentions
from takopi_transport_feishu.render import is_cancel_command, text_message_content
from takopi_transport_feishu.session import SessionStore
from takopi_transport_feishu.settings import parse_feishu_settings


def test_parse_feishu_settings_minimal() -> None:
    settings = parse_feishu_settings({"app_id": "cli_test", "app_secret": "secret123"})
    assert settings.app_id == "cli_test"
    assert settings.app_secret == "secret123"
    assert settings.session_mode == "chat"
    assert settings.require_mention_in_group is True


def test_parse_feishu_settings_missing_credentials() -> None:
    with pytest.raises(ValueError, match="app_id and app_secret"):
        parse_feishu_settings({"app_id": "cli_test"})


def test_text_message_content() -> None:
    payload = json.loads(text_message_content("hello"))
    assert payload == {"text": "hello"}


def test_strip_mentions() -> None:
    text = '<at user_id="ou_xxx">Bot</at> fix this'
    assert _strip_mentions(text) == "fix this"


def test_content_to_text_post() -> None:
    raw = json.dumps(
        {
            "title": "Title",
            "content": [[{"tag": "text", "text": "Body line"}]],
        }
    )
    assert _content_to_text(raw, message_type="post") == "Title\nBody line"


def test_access_controls() -> None:
    settings = parse_feishu_settings(
        {
            "app_id": "cli_test",
            "app_secret": "secret",
            "allowed_open_ids": ["ou_a"],
            "allowed_chat_ids": ["oc_a"],
        }
    )
    assert is_sender_allowed(settings, "ou_a")
    assert not is_sender_allowed(settings, "ou_b")
    assert is_chat_allowed(settings, "oc_a")
    assert not is_chat_allowed(settings, "oc_b")


def test_group_mention_required() -> None:
    settings = parse_feishu_settings({"app_id": "cli_test", "app_secret": "secret"})
    assert should_handle_group_message(
        settings,
        chat_type="group",
        text="@MyBot hello",
        bot_name="MyBot",
    )
    assert should_handle_group_message(
        settings,
        chat_type="group",
        text="/cursor hello",
        bot_name="MyBot",
        bot_mentioned=True,
    )
    assert not should_handle_group_message(
        settings,
        chat_type="group",
        text="hello",
        bot_name="MyBot",
    )
    assert should_handle_group_message(
        settings,
        chat_type="p2p",
        text="hello",
        bot_name="MyBot",
    )


def test_cancel_command() -> None:
    assert is_cancel_command("/cancel")
    assert is_cancel_command("cancel")
    assert not is_cancel_command("/cursor hi")


def test_session_store() -> None:
    from takopi.model import ResumeToken

    store = SessionStore()
    cursor = ResumeToken(engine="cursor", value="sess-cursor")
    qoder = ResumeToken(engine="qoder", value="sess-qoder")
    store.set(chat_id="oc_1", thread_id=None, token=cursor)
    store.set(chat_id="oc_1", thread_id=None, token=qoder)
    assert store.get(chat_id="oc_1", thread_id=None, engine="cursor") == cursor
    assert store.get(chat_id="oc_1", thread_id=None, engine="qoder") == qoder
    assert store.get(chat_id="oc_1", thread_id=None, engine="claude") is None
    store.clear(chat_id="oc_1", thread_id=None, engine="cursor")
    assert store.get(chat_id="oc_1", thread_id=None, engine="cursor") is None
    assert store.get(chat_id="oc_1", thread_id=None, engine="qoder") == qoder
    store.clear(chat_id="oc_1", thread_id=None)
    assert store.get(chat_id="oc_1", thread_id=None, engine="qoder") is None
