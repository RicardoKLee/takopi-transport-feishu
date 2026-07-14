from __future__ import annotations

import json
from pathlib import Path

import anyio
import msgspec

from .command_utils import scope_key
from .types import FeishuChatContext, FeishuThreadContext

STATE_VERSION = 1
STATE_FILENAME = "feishu_state.json"


class _ScopeState(msgspec.Struct, forbid_unknown_fields=False):
    context: dict[str, str] | None = None


class _FeishuState(msgspec.Struct, forbid_unknown_fields=False):
    version: int
    scopes: dict[str, _ScopeState] = msgspec.field(default_factory=dict)


def resolve_state_path(config_path: Path) -> Path:
    return config_path.with_name(STATE_FILENAME)


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    content = json.dumps(data, indent=2, ensure_ascii=False)
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


class FeishuStateStore:
    """Persistent context bindings for Feishu chats/threads."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = anyio.Lock()
        self._loaded = False
        self._mtime_ns: int | None = None
        self._state = _FeishuState(version=STATE_VERSION)

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
            self._state = _FeishuState(version=STATE_VERSION)
            return
        try:
            payload = msgspec.json.decode(self._path.read_bytes(), type=_FeishuState)
        except Exception:  # noqa: BLE001
            self._state = _FeishuState(version=STATE_VERSION)
            return
        self._state = payload

    def _save(self) -> None:
        payload = msgspec.to_builtins(self._state)
        _atomic_write_json(self._path, payload)
        self._mtime_ns = self._stat_mtime_ns()

    async def get_context(
        self, chat_id: str, thread_id: str | None
    ) -> FeishuChatContext | FeishuThreadContext | None:
        async with self._lock:
            self._reload_if_needed()
            entry = self._state.scopes.get(scope_key(chat_id, thread_id))
            if entry is None or entry.context is None:
                return None
            ctx = entry.context
            project = ctx.get("project")
            if not project:
                return None
            branch = ctx.get("branch")
            if branch:
                return FeishuThreadContext(
                    project=project,
                    branch=branch,
                    worktrees_dir=ctx.get("worktrees_dir", ".worktrees"),
                    default_engine=ctx.get("default_engine", "claude"),
                )
            return FeishuChatContext(
                project=project,
                worktrees_dir=ctx.get("worktrees_dir", ".worktrees"),
                default_engine=ctx.get("default_engine", "claude"),
                worktree_base=ctx.get("worktree_base", "main"),
            )

    async def set_context(
        self,
        chat_id: str,
        thread_id: str | None,
        context: FeishuChatContext | FeishuThreadContext | None,
    ) -> None:
        async with self._lock:
            self._reload_if_needed()
            key = scope_key(chat_id, thread_id)
            if key not in self._state.scopes:
                self._state.scopes[key] = _ScopeState()
            if context is None:
                self._state.scopes[key].context = None
                if self._state.scopes[key].context is None:
                    self._state.scopes.pop(key, None)
            elif isinstance(context, FeishuThreadContext):
                self._state.scopes[key].context = {
                    "project": context.project,
                    "branch": context.branch,
                    "worktrees_dir": context.worktrees_dir,
                    "default_engine": context.default_engine,
                }
            else:
                self._state.scopes[key].context = {
                    "project": context.project,
                    "worktrees_dir": context.worktrees_dir,
                    "default_engine": context.default_engine,
                    "worktree_base": context.worktree_base,
                }
            self._save()

    async def clear_context(self, chat_id: str, thread_id: str | None) -> None:
        await self.set_context(chat_id, thread_id, None)
