from __future__ import annotations

import pytest

from takopi_transport_feishu.file_transfer import (
    deny_reason,
    normalize_relative_path,
    parse_file_command,
    save_bytes_to_path,
)
from takopi_transport_feishu.state import FeishuStateStore, resolve_state_path
from takopi_transport_feishu.types import FeishuChatContext, FeishuThreadContext


def test_parse_file_command() -> None:
    assert parse_file_command("get README.md") == ("get", "README.md", None)
    assert parse_file_command("")[2] is not None


def test_normalize_relative_path_rejects_unsafe() -> None:
    assert normalize_relative_path("../secret") is None
    assert normalize_relative_path("src/main.py") is not None


def test_deny_reason() -> None:
    path = normalize_relative_path(".env")
    assert path is not None
    assert deny_reason(path, ("*.env",)) is not None


@pytest.mark.anyio
async def test_state_store_context(tmp_path) -> None:
    path = resolve_state_path(tmp_path / "takopi.toml")
    store = FeishuStateStore(path)
    await store.set_context(
        "oc_1",
        None,
        FeishuChatContext(project="sandbox", worktree_base="main"),
    )
    ctx = await store.get_context("oc_1", None)
    assert isinstance(ctx, FeishuChatContext)
    assert ctx.project == "sandbox"
    await store.set_context(
        "oc_1",
        "thr_1",
        FeishuThreadContext(project="sandbox", branch="feature-x"),
    )
    thread_ctx = await store.get_context("oc_1", "thr_1")
    assert isinstance(thread_ctx, FeishuThreadContext)
    assert thread_ctx.branch == "feature-x"


def test_save_bytes_to_path(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    result = save_bytes_to_path(
        b"hello",
        root,
        normalize_relative_path("incoming/test.txt"),  # type: ignore[arg-type]
        (".git/**",),
    )
    assert result.error is None
    assert (root / "incoming/test.txt").read_bytes() == b"hello"
