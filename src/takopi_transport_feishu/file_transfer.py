from __future__ import annotations

import io
import os
import tempfile
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DEFAULT_DENY_GLOBS = (".git/**", "*.env", ".env.*", "**/.env", "**/credentials*")
MAX_FILE_SIZE = 25 * 1024 * 1024


def file_usage() -> str:
    return "usage: `/file get <path>` or `/file put <path>` (reply to a file message)"


def parse_file_command(args_text: str) -> tuple[str | None, str, str | None]:
    from .command_utils import split_command_args

    tokens = split_command_args(args_text)
    if not tokens:
        return None, "", file_usage()
    command = tokens[0].lower()
    rest = " ".join(tokens[1:]).strip()
    if command not in {"put", "get"}:
        return None, rest, file_usage()
    return command, rest, None


def normalize_relative_path(value: str) -> Path | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.startswith("~"):
        return None
    path = Path(cleaned)
    if path.is_absolute():
        return None
    parts = [part for part in path.parts if part not in {"", "."}]
    if not parts or ".." in parts or ".git" in parts:
        return None
    return Path(*parts)


def resolve_path_within_root(root: Path, rel_path: Path) -> Path | None:
    root_resolved = root.resolve(strict=False)
    target = (root / rel_path).resolve(strict=False)
    if not target.is_relative_to(root_resolved):
        return None
    return target


def deny_reason(rel_path: Path, deny_globs: Sequence[str]) -> str | None:
    if ".git" in rel_path.parts:
        return ".git/**"
    posix = PurePosixPath(rel_path.as_posix())
    for pattern in deny_globs:
        if posix.match(pattern):
            return pattern
    return None


def format_bytes(value: int) -> str:
    size = max(0.0, float(value))
    units = ("b", "kb", "mb", "gb", "tb")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "b":
                return f"{int(size)} b"
            if size < 10:
                return f"{size:.1f} {unit}"
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{int(size)} b"


def default_upload_name(filename: str | None) -> str:
    name = Path(filename or "").name if filename else ""
    return name or "upload.bin"


def write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", delete=False, dir=path.parent, prefix=".takopi-upload-"
    ) as handle:
        handle.write(payload)
        temp_name = handle.name
    Path(temp_name).replace(path)


class ZipTooLargeError(Exception):
    pass


def zip_directory(
    root: Path,
    rel_path: Path,
    deny_globs: Sequence[str],
    *,
    max_bytes: int | None = None,
) -> bytes:
    target = root / rel_path
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for dirpath, _, filenames in os.walk(target, followlinks=False):
            dir_path = Path(dirpath)
            for filename in filenames:
                item = dir_path / filename
                if item.is_symlink() or not item.is_file():
                    continue
                rel_item = rel_path / item.relative_to(target)
                if deny_reason(rel_item, deny_globs) is not None:
                    continue
                archive.write(item, arcname=rel_item.as_posix())
                if max_bytes is not None and buffer.tell() > max_bytes:
                    raise ZipTooLargeError()
    payload = buffer.getvalue()
    if max_bytes is not None and len(payload) > max_bytes:
        raise ZipTooLargeError()
    return payload


@dataclass(slots=True)
class PutAttachmentResult:
    rel_path: Path | None
    size: int | None
    overwritten: bool = False
    error: str | None = None


def save_bytes_to_path(
    payload: bytes,
    run_root: Path,
    rel_path: Path,
    deny_globs: Sequence[str],
    *,
    max_bytes: int = MAX_FILE_SIZE,
    force: bool = False,
) -> PutAttachmentResult:
    if len(payload) > max_bytes:
        return PutAttachmentResult(error="file is too large to upload")
    deny_rule = deny_reason(rel_path, deny_globs)
    if deny_rule is not None:
        return PutAttachmentResult(error=f"path denied by rule: {deny_rule}")
    target = resolve_path_within_root(run_root, rel_path)
    if target is None:
        return PutAttachmentResult(error="upload path escapes the project root")
    if target.exists() and target.is_dir():
        return PutAttachmentResult(error=f"`{rel_path.as_posix()}` is a directory")
    overwritten = target.exists()
    if overwritten and not force:
        return PutAttachmentResult(
            error="file already exists (add `force` to overwrite)"
        )
    try:
        write_bytes_atomic(target, payload)
    except OSError as exc:
        return PutAttachmentResult(error=f"failed to save file: {exc}")
    return PutAttachmentResult(
        rel_path=rel_path,
        size=len(payload),
        overwritten=overwritten,
    )
