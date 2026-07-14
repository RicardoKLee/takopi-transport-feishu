from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FeishuChatContext:
    project: str
    worktrees_dir: str = ".worktrees"
    default_engine: str = "claude"
    worktree_base: str = "main"


@dataclass(frozen=True, slots=True)
class FeishuThreadContext:
    project: str
    branch: str
    worktrees_dir: str = ".worktrees"
    default_engine: str = "claude"
