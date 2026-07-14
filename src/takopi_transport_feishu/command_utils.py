from __future__ import annotations

import shlex
from typing import Literal

from takopi.model import EngineId

ScopeKind = Literal["chat", "thread"]


def scope_key(chat_id: str, thread_id: str | None) -> str:
    if thread_id:
        return f"{chat_id}:{thread_id}"
    return chat_id


def parent_chat_id(chat_id: str, thread_id: str | None) -> str:
    del thread_id
    return chat_id


def resolve_target_scope(chat_id: str, thread_id: str | None) -> tuple[str, ScopeKind]:
    if thread_id:
        return scope_key(chat_id, thread_id), "thread"
    return scope_key(chat_id, None), "chat"


def split_command_args(text: str) -> tuple[str, ...]:
    stripped = text.strip()
    if not stripped:
        return ()
    try:
        return tuple(shlex.split(stripped))
    except ValueError:
        return tuple(stripped.split())


def normalize_branch_name(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("@"):
        cleaned = cleaned[1:].strip()
    return cleaned


def parse_set_args(
    tokens: tuple[str, ...],
    *,
    engine_ids: set[str],
) -> tuple[EngineId | None, str | None]:
    if len(tokens) < 2:
        return None, None
    first = tokens[1].strip().lower()
    if first in engine_ids:
        if len(tokens) < 3:
            return first, None
        return first, " ".join(tokens[2:]).strip() or None
    return None, " ".join(tokens[1:]).strip() or None
