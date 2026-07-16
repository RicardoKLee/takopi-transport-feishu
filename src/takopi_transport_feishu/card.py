"""Build Feishu CardKit 2.0 card JSON from markdown text.

Uses ``schema: "2.0"`` with three CardKit element types, mirroring
the reference project (lark-coding-agent-bridge):

- ``markdown`` elements for text content
- ``collapsible_panel`` elements for fenced code blocks (expanded
  by default so content is immediately visible)
- ``button`` elements for the stop button (sends a card action
  callback when clicked)

LaTeX block formulas (``$$...$$`` and Feishu's ``$$$$...$$$$``) are
converted to ```` ```latex ```` code blocks before parsing, so they
render as collapsible panels with syntax highlighting.

``streaming_mode`` in the card config enables Feishu's streaming
animation during live updates.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = [
    "build_card",
    "card_message_content",
    "MAX_ELEMENT_CHARS",
]

MAX_ELEMENT_CHARS = 4000

_FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
_QUAD_BLOCK_RE = re.compile(r"\$\$\$\$(.+?)\$\$\$\$", re.DOTALL)
_DBL_BLOCK_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)


def _convert_formulas(text: str) -> str:
    """Convert ``$$...$$`` and ``$$$$...$$$$`` formulas to latex code blocks."""
    text = _QUAD_BLOCK_RE.sub(lambda m: f"```latex\n{m.group(1).strip()}\n```", text)
    text = _DBL_BLOCK_RE.sub(lambda m: f"```latex\n{m.group(1).strip()}\n```", text)
    return text


def build_card(
    text: str,
    *,
    streaming: bool = False,
    summary: str | None = None,
    show_stop_button: bool = False,
) -> dict[str, Any]:
    """Build a Feishu CardKit 2.0 card dict from markdown *text*."""
    content = text.strip() or " "
    content = _convert_formulas(content)
    elements: list[dict[str, Any]] = []

    for segment in _parse_segments(content):
        if segment["kind"] == "code":
            elements.append(_code_panel(segment["lang"], segment["content"]))
        else:
            for chunk in _split_long_text(segment["content"], MAX_ELEMENT_CHARS):
                elements.append({"tag": "markdown", "content": chunk})

    if show_stop_button:
        elements.append(_stop_button())

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {
            "streaming_mode": streaming,
        },
        "body": {"elements": elements},
    }

    summary_text = summary or _extract_summary(text)
    if summary_text:
        card["config"]["summary"] = {"content": summary_text}

    return card


def card_message_content(card: dict[str, Any]) -> str:
    """Serialize a card dict into the JSON string Feishu's API expects."""
    return json.dumps(card, ensure_ascii=False)


# --- segment parsing -------------------------------------------------

def _parse_segments(text: str) -> list[dict[str, Any]]:
    """Split *text* into ordered ``text`` and ``code`` segments."""
    segments: list[dict[str, Any]] = []
    last_end = 0

    for match in _FENCE_RE.finditer(text):
        if match.start() > last_end:
            before = text[last_end:match.start()]
            if before.strip():
                segments.append({"kind": "text", "content": before})

        lang = match.group(1) or ""
        code = match.group(2)
        segments.append({"kind": "code", "lang": lang, "content": code})
        last_end = match.end()

    if last_end < len(text):
        remaining = text[last_end:]
        if remaining.strip():
            segments.append({"kind": "text", "content": remaining})

    if not segments:
        segments.append({"kind": "text", "content": text})

    return segments


# --- element builders ------------------------------------------------

def _code_panel(lang: str, code: str) -> dict[str, Any]:
    """Build a collapsible_panel for a fenced code block."""
    label = lang or "code"
    fenced = f"```{lang}\n{code}```" if lang else f"```\n{code}```"
    return {
        "tag": "collapsible_panel",
        "expanded": True,
        "header": {
            "title": {"tag": "markdown", "content": f"📄 **{label}**"},
            "vertical_align": "center",
            "icon": {
                "tag": "standard_icon",
                "token": "down-small-ccm_outlined",
                "size": "16px 16px",
            },
            "icon_position": "follow_text",
            "icon_expanded_angle": -180,
        },
        "border": {"color": "grey", "corner_radius": "5px"},
        "vertical_spacing": "8px",
        "padding": "8px 8px 8px 8px",
        "elements": [{"tag": "markdown", "content": fenced}],
    }


def _stop_button() -> dict[str, Any]:
    """Build a danger stop button that sends a card action callback."""
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": "⏹ 终止"},
        "type": "danger",
        "behaviors": [{"type": "callback", "value": {"cmd": "stop"}}],
    }


# --- helpers ---------------------------------------------------------

def _extract_summary(text: str) -> str | None:
    """Extract a short summary from the first meaningful line of *text*."""
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        clean = re.sub(r"[*`_~#>|]", "", stripped).strip()
        if clean:
            return clean[:77] + "..." if len(clean) > 80 else clean
    return None


def _split_long_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        candidate = current + line
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return chunks
