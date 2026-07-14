from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .chat_prefs import FeishuChatPrefsStore

REASONING_LEVELS = frozenset({"minimal", "low", "medium", "high", "xhigh"})
REASONING_ENGINES = frozenset({"codex"})


@dataclass(frozen=True, slots=True)
class ResolvedOverrides:
    model: str | None = None
    reasoning: str | None = None
    source_model: str | None = None
    source_reasoning: str | None = None


async def resolve_overrides(
    prefs_store: FeishuChatPrefsStore,
    *,
    chat_id: str,
    thread_id: str | None,
    engine_id: str,
) -> ResolvedOverrides:
    model: str | None = None
    reasoning: str | None = None
    source_model: str | None = None
    source_reasoning: str | None = None

    if thread_id is not None:
        thread_model = await prefs_store.get_model_override(
            chat_id, thread_id, engine_id
        )
        if thread_model is not None:
            model = thread_model
            source_model = "thread"
        thread_reasoning = await prefs_store.get_reasoning_override(
            chat_id, thread_id, engine_id
        )
        if thread_reasoning is not None:
            reasoning = thread_reasoning
            source_reasoning = "thread"

    if model is None:
        chat_model = await prefs_store.get_model_override(chat_id, None, engine_id)
        if chat_model is not None:
            model = chat_model
            source_model = "chat"

    if reasoning is None:
        chat_reasoning = await prefs_store.get_reasoning_override(
            chat_id, None, engine_id
        )
        if chat_reasoning is not None:
            reasoning = chat_reasoning
            source_reasoning = "chat"

    return ResolvedOverrides(
        model=model,
        reasoning=reasoning,
        source_model=source_model,
        source_reasoning=source_reasoning,
    )


async def resolve_trigger_mode(
    prefs_store: FeishuChatPrefsStore,
    *,
    chat_id: str,
    thread_id: str | None,
    default_mode: Literal["all", "mentions"] = "all",
) -> Literal["all", "mentions"]:
    if thread_id is not None:
        thread_mode = await prefs_store.get_trigger_mode(chat_id, thread_id)
        if thread_mode in {"all", "mentions"}:
            return thread_mode

    chat_mode = await prefs_store.get_trigger_mode(chat_id, None)
    if chat_mode in {"all", "mentions"}:
        return chat_mode

    return default_mode


async def resolve_default_engine(
    prefs_store: FeishuChatPrefsStore,
    *,
    chat_id: str,
    thread_id: str | None,
    config_default: str | None,
) -> tuple[str | None, str | None]:
    if thread_id is not None:
        thread_engine = await prefs_store.get_default_engine(chat_id, thread_id)
        if thread_engine is not None:
            return thread_engine, "thread"

    chat_engine = await prefs_store.get_default_engine(chat_id, None)
    if chat_engine is not None:
        return chat_engine, "chat"

    if config_default is not None:
        return config_default, "config"

    return None, None


def supports_reasoning(engine_id: str) -> bool:
    return engine_id in REASONING_ENGINES


def is_valid_reasoning_level(level: str) -> bool:
    return level in REASONING_LEVELS
