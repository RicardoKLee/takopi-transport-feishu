from __future__ import annotations

import json
from pathlib import Path

import anyio
import msgspec

from .command_utils import scope_key

STATE_VERSION = 1
STATE_FILENAME = "feishu_chat_prefs_state.json"


class _ChatPrefs(msgspec.Struct, forbid_unknown_fields=False):
    default_engine: str | None = None
    model_overrides: dict[str, str] | None = None
    reasoning_overrides: dict[str, str] | None = None
    trigger_mode: str | None = None


class _ChatPrefsState(msgspec.Struct, forbid_unknown_fields=False):
    version: int
    chats: dict[str, _ChatPrefs] = msgspec.field(default_factory=dict)


def resolve_prefs_path(config_path: Path) -> Path:
    return config_path.with_name(STATE_FILENAME)


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    content = json.dumps(data, indent=2, ensure_ascii=False)
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


class FeishuChatPrefsStore:
    """Per-chat/thread preferences for Feishu transport."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = anyio.Lock()
        self._loaded = False
        self._mtime_ns: int | None = None
        self._state = _ChatPrefsState(version=STATE_VERSION)

    def _stat_mtime_ns(self) -> int | None:
        try:
            return self._path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def _reload_if_needed(self) -> None:
        current = self._stat_mtime_ns()
        if self._loaded and current == self._mtime_ns:
            return
        self._load()

    def _load(self) -> None:
        self._loaded = True
        self._mtime_ns = self._stat_mtime_ns()
        if self._mtime_ns is None:
            self._state = _ChatPrefsState(version=STATE_VERSION)
            return
        try:
            payload = msgspec.json.decode(self._path.read_bytes(), type=_ChatPrefsState)
        except Exception:  # noqa: BLE001
            self._state = _ChatPrefsState(version=STATE_VERSION)
            return
        if payload.version < STATE_VERSION:
            payload = _ChatPrefsState(version=STATE_VERSION, chats=payload.chats)
        self._state = payload

    def _save(self) -> None:
        payload = msgspec.to_builtins(self._state)
        _atomic_write_json(self._path, payload)
        self._mtime_ns = self._stat_mtime_ns()

    def _maybe_prune_locked(self, key: str) -> None:
        entry = self._state.chats.get(key)
        if entry is None:
            return
        if (
            entry.default_engine
            or entry.model_overrides
            or entry.reasoning_overrides
            or entry.trigger_mode is not None
        ):
            return
        del self._state.chats[key]

    def _entry_locked(self, key: str) -> _ChatPrefs:
        return self._state.chats.setdefault(key, _ChatPrefs())

    async def get_default_engine(
        self, chat_id: str, thread_id: str | None = None
    ) -> str | None:
        async with self._lock:
            self._reload_if_needed()
            entry = self._state.chats.get(scope_key(chat_id, thread_id))
            if entry is None or not entry.default_engine:
                return None
            return entry.default_engine.strip().lower() or None

    async def set_default_engine(
        self, chat_id: str, engine: str | None, *, thread_id: str | None = None
    ) -> None:
        async with self._lock:
            self._reload_if_needed()
            key = scope_key(chat_id, thread_id)
            if engine is None:
                entry = self._state.chats.get(key)
                if entry is not None:
                    entry.default_engine = None
                    self._maybe_prune_locked(key)
            else:
                normalized = engine.strip().lower()
                if normalized:
                    self._entry_locked(key).default_engine = normalized
                else:
                    entry = self._state.chats.get(key)
                    if entry is not None:
                        entry.default_engine = None
                        self._maybe_prune_locked(key)
            self._save()

    async def clear_default_engine(
        self, chat_id: str, *, thread_id: str | None = None
    ) -> None:
        await self.set_default_engine(chat_id, None, thread_id=thread_id)

    async def get_model_override(
        self, chat_id: str, thread_id: str | None, engine_id: str
    ) -> str | None:
        async with self._lock:
            self._reload_if_needed()
            entry = self._state.chats.get(scope_key(chat_id, thread_id))
            if entry is None or not entry.model_overrides:
                return None
            return entry.model_overrides.get(engine_id)

    async def set_model_override(
        self,
        chat_id: str,
        thread_id: str | None,
        engine_id: str,
        model: str | None,
    ) -> None:
        async with self._lock:
            self._reload_if_needed()
            key = scope_key(chat_id, thread_id)
            if model is None:
                entry = self._state.chats.get(key)
                if entry and entry.model_overrides:
                    entry.model_overrides.pop(engine_id, None)
                    if not entry.model_overrides:
                        entry.model_overrides = None
                    self._maybe_prune_locked(key)
            else:
                entry = self._entry_locked(key)
                if entry.model_overrides is None:
                    entry.model_overrides = {}
                entry.model_overrides[engine_id] = model
            self._save()

    async def get_reasoning_override(
        self, chat_id: str, thread_id: str | None, engine_id: str
    ) -> str | None:
        async with self._lock:
            self._reload_if_needed()
            entry = self._state.chats.get(scope_key(chat_id, thread_id))
            if entry is None or not entry.reasoning_overrides:
                return None
            return entry.reasoning_overrides.get(engine_id)

    async def set_reasoning_override(
        self,
        chat_id: str,
        thread_id: str | None,
        engine_id: str,
        level: str | None,
    ) -> None:
        async with self._lock:
            self._reload_if_needed()
            key = scope_key(chat_id, thread_id)
            if level is None:
                entry = self._state.chats.get(key)
                if entry and entry.reasoning_overrides:
                    entry.reasoning_overrides.pop(engine_id, None)
                    if not entry.reasoning_overrides:
                        entry.reasoning_overrides = None
                    self._maybe_prune_locked(key)
            else:
                entry = self._entry_locked(key)
                if entry.reasoning_overrides is None:
                    entry.reasoning_overrides = {}
                entry.reasoning_overrides[engine_id] = level
            self._save()

    async def get_trigger_mode(self, chat_id: str, thread_id: str | None) -> str | None:
        async with self._lock:
            self._reload_if_needed()
            entry = self._state.chats.get(scope_key(chat_id, thread_id))
            if entry is None:
                return None
            return entry.trigger_mode

    async def set_trigger_mode(
        self, chat_id: str, thread_id: str | None, mode: str | None
    ) -> None:
        async with self._lock:
            self._reload_if_needed()
            key = scope_key(chat_id, thread_id)
            if mode is None:
                entry = self._state.chats.get(key)
                if entry is not None:
                    entry.trigger_mode = None
                    self._maybe_prune_locked(key)
            else:
                self._entry_locked(key).trigger_mode = mode
            self._save()

    async def get_all_overrides(
        self, chat_id: str, thread_id: str | None
    ) -> tuple[dict[str, str] | None, dict[str, str] | None, str | None, str | None]:
        async with self._lock:
            self._reload_if_needed()
            entry = self._state.chats.get(scope_key(chat_id, thread_id))
            if entry is None:
                return None, None, None, None
            return (
                entry.model_overrides,
                entry.reasoning_overrides,
                entry.trigger_mode,
                entry.default_engine,
            )

    async def clear_scope(self, chat_id: str, thread_id: str | None) -> None:
        async with self._lock:
            self._reload_if_needed()
            key = scope_key(chat_id, thread_id)
            self._state.chats.pop(key, None)
            self._save()
