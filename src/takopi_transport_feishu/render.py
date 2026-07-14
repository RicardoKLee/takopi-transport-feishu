from __future__ import annotations

import json
import re

from takopi.markdown import MarkdownParts, assemble_markdown_parts

MAX_MESSAGE_CHARS = 4000
MAX_BODY_CHARS = 3500

_FENCE_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>[`~]{3,})(?P<info>.*)$")


class _FenceState:
    __slots__ = ("fence", "indent", "header")

    def __init__(self, fence: str, indent: str, header: str) -> None:
        self.fence = fence
        self.indent = indent
        self.header = header


def _update_fence_state(line: str, state: _FenceState | None) -> _FenceState | None:
    match = _FENCE_RE.match(line)
    if match is None:
        return state
    fence = match.group("fence")
    indent = match.group("indent")
    if state is None:
        return _FenceState(fence=fence, indent=indent, header=line)
    if fence[0] == state.fence[0] and len(fence) >= len(state.fence):
        return None
    return state


def _scan_fence_state(text: str, state: _FenceState | None) -> _FenceState | None:
    for line in text.splitlines():
        state = _update_fence_state(line, state)
    return state


def _ensure_trailing_newline(text: str) -> str:
    if text.endswith("\n") or text.endswith("\r"):
        return text
    return text + "\n"


def _close_fence_chunk(text: str, state: _FenceState) -> str:
    return _ensure_trailing_newline(text) + f"{state.indent}{state.fence}\n"


def _reopen_fence_prefix(state: _FenceState) -> str:
    return f"{state.header}\n"


def _split_long_line(line: str, max_chars: int) -> list[str]:
    if len(line) <= max_chars:
        return [line]
    chunks: list[str] = []
    start = 0
    while start < len(line):
        chunks.append(line[start : start + max_chars])
        start += max_chars
    return chunks


def _split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    current = ""
    fence_state: _FenceState | None = None

    for line in lines:
        candidate = current + line
        if len(candidate) <= max_chars:
            current = candidate
            fence_state = _scan_fence_state(line, fence_state)
            continue

        if current:
            if fence_state is not None:
                chunks.append(_close_fence_chunk(current, fence_state))
                current = _reopen_fence_prefix(fence_state)
                fence_state = _scan_fence_state(current, None)
            else:
                chunks.append(current)
                current = ""

        if len(line) > max_chars:
            for piece in _split_long_line(line, max_chars):
                if current and len(current) + len(piece) > max_chars:
                    chunks.append(current)
                    current = piece
                else:
                    current += piece
            fence_state = _scan_fence_state(current, None)
            continue

        current += line
        fence_state = _scan_fence_state(line, fence_state)

    if current:
        if fence_state is not None:
            chunks.append(_close_fence_chunk(current, fence_state))
        else:
            chunks.append(current)

    return [chunk for chunk in chunks if chunk.strip()]


def prepare_feishu(parts: MarkdownParts) -> str:
    text = assemble_markdown_parts(parts)
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    return _split_text(text, MAX_MESSAGE_CHARS)[0]


def prepare_feishu_multi(
    parts: MarkdownParts,
    *,
    max_body_chars: int = MAX_BODY_CHARS,
) -> list[str]:
    text = assemble_markdown_parts(parts)
    return _split_text(text, max_body_chars)


def text_message_content(text: str) -> str:
    return json.dumps({"text": text}, ensure_ascii=False)


def is_cancel_command(text: str) -> bool:
    stripped = text.strip().lower()
    if stripped in {"cancel", "/cancel"}:
        return True
    return stripped.startswith("/cancel@") or stripped.startswith("/cancel ")
