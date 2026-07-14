from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class FeishuFilesSettings:
    enabled: bool = False
    deny_globs: tuple[str, ...] = (
        ".git/**",
        "*.env",
        ".env.*",
        "**/.env",
        "**/credentials*",
    )
    max_upload_bytes: int = 20 * 1024 * 1024
    uploads_dir: str = "incoming"


@dataclass(slots=True)
class FeishuTransportSettings:
    app_id: str
    app_secret: str
    chat_id: str | None = None
    session_mode: Literal["stateless", "chat"] = "chat"
    show_resume_line: bool = True
    message_overflow: Literal["split", "trim"] = "split"
    require_mention_in_group: bool = True
    allowed_open_ids: tuple[str, ...] = ()
    allowed_chat_ids: tuple[str, ...] = ()
    domain: str = "https://open.feishu.cn"
    log_level: str = "INFO"
    trigger_mode_default: Literal["all", "mentions"] = "all"
    files: FeishuFilesSettings = field(default_factory=FeishuFilesSettings)

    @property
    def max_text_chars(self) -> int:
        return 4000


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        cleaned = value.strip()
        return (cleaned,) if cleaned else ()
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return tuple(out)
    return ()


def parse_feishu_settings(raw: dict[str, Any]) -> FeishuTransportSettings:
    app_id = str(raw.get("app_id") or "").strip()
    app_secret = str(raw.get("app_secret") or "").strip()
    if not app_id or not app_secret:
        raise ValueError("transports.feishu requires app_id and app_secret")

    chat_id = raw.get("chat_id")
    chat_id_str = str(chat_id).strip() if chat_id is not None else None
    if chat_id_str == "":
        chat_id_str = None

    session_mode_raw = str(raw.get("session_mode", "chat")).strip().lower()
    session_mode: Literal["stateless", "chat"] = (
        "stateless" if session_mode_raw == "stateless" else "chat"
    )

    overflow_raw = str(raw.get("message_overflow", "split")).strip().lower()
    message_overflow: Literal["split", "trim"] = (
        "trim" if overflow_raw == "trim" else "split"
    )

    domain = str(raw.get("domain") or "https://open.feishu.cn").strip()
    log_level = str(raw.get("log_level") or "INFO").strip().upper()

    trigger_raw = str(raw.get("trigger_mode_default", "all")).strip().lower()
    trigger_mode_default: Literal["all", "mentions"] = (
        "mentions" if trigger_raw == "mentions" else "all"
    )

    files_raw = raw.get("files")
    files = FeishuFilesSettings()
    if isinstance(files_raw, dict):
        deny = files_raw.get("deny_globs")
        deny_globs = _as_str_tuple(deny) if deny is not None else files.deny_globs
        max_upload = files_raw.get("max_upload_bytes", files.max_upload_bytes)
        uploads_dir = str(files_raw.get("uploads_dir") or files.uploads_dir).strip()
        files = FeishuFilesSettings(
            enabled=bool(files_raw.get("enabled", False)),
            deny_globs=deny_globs or files.deny_globs,
            max_upload_bytes=int(max_upload),
            uploads_dir=uploads_dir or files.uploads_dir,
        )

    return FeishuTransportSettings(
        app_id=app_id,
        app_secret=app_secret,
        chat_id=chat_id_str,
        session_mode=session_mode,
        show_resume_line=bool(raw.get("show_resume_line", True)),
        message_overflow=message_overflow,
        require_mention_in_group=bool(raw.get("require_mention_in_group", True)),
        allowed_open_ids=_as_str_tuple(raw.get("allowed_open_ids")),
        allowed_chat_ids=_as_str_tuple(raw.get("allowed_chat_ids")),
        domain=domain,
        log_level=log_level,
        trigger_mode_default=trigger_mode_default,
        files=files,
    )
