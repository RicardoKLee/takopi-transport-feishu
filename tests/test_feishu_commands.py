from __future__ import annotations

import pytest

from takopi_transport_feishu.chat_prefs import FeishuChatPrefsStore, resolve_prefs_path
from takopi_transport_feishu.command_handlers import (
    AGENT_USAGE,
    MODEL_USAGE,
    engine_only_usage,
    parse_slash_command,
    resolve_chat_engine,
)
from takopi_transport_feishu.command_utils import split_command_args
from takopi_transport_feishu.overrides import (
    is_valid_reasoning_level,
    resolve_overrides,
    resolve_trigger_mode,
    supports_reasoning,
)


def test_parse_slash_command() -> None:
    assert parse_slash_command("/help") == ("help", "")
    assert parse_slash_command("/agent set qoder") == ("agent", "set qoder")
    assert parse_slash_command("/model set cursor auto") == (
        "model",
        "set cursor auto",
    )
    assert parse_slash_command("/qoder hi") == ("qoder", "hi")
    assert parse_slash_command("hello") == (None, "hello")


def test_split_command_args() -> None:
    assert split_command_args("set qoder") == ("set", "qoder")
    assert split_command_args('set cursor "auto model"') == (
        "set",
        "cursor",
        "auto model",
    )


def test_engine_only_usage() -> None:
    assert "qoder" in engine_only_usage("qoder")


def test_reasoning_helpers() -> None:
    assert supports_reasoning("codex")
    assert not supports_reasoning("claude")
    assert is_valid_reasoning_level("high")
    assert not is_valid_reasoning_level("extreme")


@pytest.mark.anyio
async def test_chat_prefs_roundtrip(tmp_path) -> None:
    path = resolve_prefs_path(tmp_path / "takopi.toml")
    store = FeishuChatPrefsStore(path)
    assert await store.get_default_engine("oc_1") is None
    await store.set_default_engine("oc_1", "qoder")
    assert await store.get_default_engine("oc_1") == "qoder"
    await store.set_model_override("oc_1", None, "cursor", "auto")
    assert await store.get_model_override("oc_1", None, "cursor") == "auto"
    await store.set_trigger_mode("oc_1", None, "mentions")
    assert await store.get_trigger_mode("oc_1", None) == "mentions"

    store2 = FeishuChatPrefsStore(path)
    assert await store2.get_default_engine("oc_1") == "qoder"
    assert await store2.get_model_override("oc_1", None, "cursor") == "auto"
    await store2.clear_default_engine("oc_1")
    assert await store2.get_default_engine("oc_1") is None


@pytest.mark.anyio
async def test_resolve_overrides_cascade(tmp_path) -> None:
    path = resolve_prefs_path(tmp_path / "takopi.toml")
    store = FeishuChatPrefsStore(path)
    await store.set_model_override("oc_1", None, "cursor", "chat-model")
    await store.set_model_override("oc_1", "thr_1", "cursor", "thread-model")
    resolved = await resolve_overrides(
        store, chat_id="oc_1", thread_id="thr_1", engine_id="cursor"
    )
    assert resolved.model == "thread-model"
    assert resolved.source_model == "thread"


@pytest.mark.anyio
async def test_resolve_trigger_mode(tmp_path) -> None:
    path = resolve_prefs_path(tmp_path / "takopi.toml")
    store = FeishuChatPrefsStore(path)
    await store.set_trigger_mode("oc_1", None, "mentions")
    mode = await resolve_trigger_mode(
        store, chat_id="oc_1", thread_id=None, default_mode="all"
    )
    assert mode == "mentions"


@pytest.mark.anyio
async def test_resolve_chat_engine_prefers_explicit() -> None:
    class Runtime:
        default_engine = "claude"
        engine_ids = ("claude", "qoder")

    class Prefs:
        async def get_default_engine(self, chat_id: str, thread_id=None):
            del chat_id, thread_id
            return "qoder"

    engine = await resolve_chat_engine(
        runtime=Runtime(),
        chat_prefs=Prefs(),
        chat_id="oc_1",
        thread_id=None,
        explicit_engine="cursor",
        default_engine_override="claude",
    )
    assert engine == "cursor"


def test_usage_strings() -> None:
    assert "/agent set" in AGENT_USAGE
    assert "/model set" in MODEL_USAGE
