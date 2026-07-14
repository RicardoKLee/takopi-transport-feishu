from __future__ import annotations

from typing import Literal

from takopi.markdown import MarkdownFormatter
from takopi.progress import ProgressState
from takopi.transport import RenderedMessage

from .render import MAX_BODY_CHARS, prepare_feishu, prepare_feishu_multi

__all__ = ["FeishuPresenter"]


def _is_cancelled_label(label: str) -> bool:
    stripped = label.strip()
    if stripped.startswith("`") and stripped.endswith("`") and len(stripped) >= 2:
        stripped = stripped[1:-1]
    return stripped.lower() == "cancelled"


class FeishuPresenter:
    def __init__(
        self,
        *,
        formatter: MarkdownFormatter | None = None,
        message_overflow: Literal["split", "trim"] = "split",
    ) -> None:
        self._formatter = formatter or MarkdownFormatter()
        self._message_overflow = message_overflow

    def render_progress(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        label: str = "working",
    ) -> RenderedMessage:
        parts = self._formatter.render_progress_parts(
            state, elapsed_s=elapsed_s, label=label
        )
        text = prepare_feishu(parts)
        return RenderedMessage(text=text, extra={})

    def render_final(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        status: str,
        answer: str,
    ) -> RenderedMessage:
        parts = self._formatter.render_final_parts(
            state, elapsed_s=elapsed_s, status=status, answer=answer
        )
        if self._message_overflow == "split":
            messages = prepare_feishu_multi(parts, max_body_chars=MAX_BODY_CHARS)
            text = messages[0]
            extra: dict = {}
            if len(messages) > 1:
                followups = [
                    RenderedMessage(text=msg, extra={}) for msg in messages[1:]
                ]
                extra["followups"] = followups
            return RenderedMessage(text=text, extra=extra)
        text = prepare_feishu(parts)
        return RenderedMessage(text=text, extra={})
