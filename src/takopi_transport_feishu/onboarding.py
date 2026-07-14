from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import questionary
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from takopi.backends import EngineBackend, SetupIssue
from takopi.backends_helpers import install_issue
from takopi.config import ConfigError, dump_toml, ensure_table, read_config, write_config
from takopi.engines import list_backends
from takopi.logging import suppress_logs
from takopi.settings import HOME_CONFIG_PATH, load_settings
from takopi.transports import SetupResult

__all__ = ["check_setup", "interactive_setup", "mask_secret"]


def _display_path(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


_CREATE_CONFIG_TITLE = "create a config"
_CONFIGURE_FEISHU_TITLE = "configure feishu"


def config_issue(path: Path, *, title: str) -> SetupIssue:
    return SetupIssue(title, (f"   {_display_path(path)}",))


def _require_feishu(settings, config_path: Path) -> dict[str, Any]:
    transports = getattr(settings, "transports", None)
    if transports is None:
        raise ConfigError(f"no transports configured in {config_path}")

    feishu_config = getattr(transports, "feishu", None)
    if feishu_config is None:
        extra = getattr(transports, "model_extra", {}) or {}
        feishu_config = extra.get("feishu")

    if feishu_config is None:
        raise ConfigError(f"feishu transport not configured in {config_path}")

    if isinstance(feishu_config, dict):
        if not feishu_config.get("app_id") or not feishu_config.get("app_secret"):
            raise ConfigError("transports.feishu requires app_id and app_secret")
        return feishu_config

    app_id = getattr(feishu_config, "app_id", None)
    app_secret = getattr(feishu_config, "app_secret", None)
    if not app_id or not app_secret:
        raise ConfigError("transports.feishu requires app_id and app_secret")
    return {"app_id": app_id, "app_secret": app_secret}


def check_setup(
    backend: EngineBackend,
    *,
    transport_override: str | None = None,
) -> SetupResult:
    issues: list[SetupIssue] = []
    config_path = HOME_CONFIG_PATH
    cmd = backend.cli_cmd or backend.id
    backend_issues: list[SetupIssue] = []
    if shutil.which(cmd) is None:
        backend_issues.append(install_issue(cmd, backend.install_cmd))

    try:
        settings, config_path = load_settings()
        if transport_override:
            settings = settings.model_copy(update={"transport": transport_override})
        try:
            _require_feishu(settings, config_path)
        except ConfigError:
            issues.append(config_issue(config_path, title=_CONFIGURE_FEISHU_TITLE))
    except ConfigError:
        issues.extend(backend_issues)
        title = (
            _CONFIGURE_FEISHU_TITLE
            if config_path.exists() and config_path.is_file()
            else _CREATE_CONFIG_TITLE
        )
        issues.append(config_issue(config_path, title=title))
        return SetupResult(issues=issues, config_path=config_path)

    issues.extend(backend_issues)
    return SetupResult(issues=issues, config_path=config_path)


def mask_secret(value: str) -> str:
    value = value.strip()
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


async def _confirm(message: str, *, default: bool = True) -> bool | None:
    return await questionary.confirm(message, default=default).ask_async()


def _render_engine_table(console: Console) -> list[tuple[str, bool, str | None]]:
    backends = list_backends()
    rows: list[tuple[str, bool, str | None]] = []
    table = Table(show_header=True, header_style="bold", box=box.SIMPLE)
    table.add_column("agent")
    table.add_column("status")
    table.add_column("install command")
    for backend in backends:
        cmd = backend.cli_cmd or backend.id
        installed = shutil.which(cmd) is not None
        status = "[green]✓ installed[/]" if installed else "[dim]✗ not found[/]"
        rows.append((backend.id, installed, backend.install_cmd))
        table.add_row(
            backend.id,
            status,
            "" if installed else (backend.install_cmd or "-"),
        )
    console.print(table)
    return rows


async def interactive_setup(*, force: bool) -> bool:
    console = Console()
    config_path = HOME_CONFIG_PATH

    if config_path.exists() and not force:
        console.print(
            f"config already exists at {_display_path(config_path)}. "
            "use --onboard to reconfigure."
        )
        return True

    if config_path.exists() and force:
        overwrite = await _confirm(
            f"update existing config at {_display_path(config_path)}?",
            default=False,
        )
        if not overwrite:
            return False

    with suppress_logs():
        panel = Panel(
            "let's set up your feishu bot.",
            title="welcome to takopi-feishu!",
            border_style="blue",
            padding=(1, 2),
            expand=False,
        )
        console.print(panel)

        console.print("step 1: feishu app credentials\n")
        app_id = await questionary.text("enter app_id (cli_xxx):").ask_async()
        if app_id is None:
            return False
        app_id = app_id.strip()
        if not app_id:
            console.print("  app_id cannot be empty")
            return False

        app_secret = await questionary.password("enter app_secret:").ask_async()
        if app_secret is None:
            return False
        app_secret = app_secret.strip()
        if not app_secret:
            console.print("  app_secret cannot be empty")
            return False

        chat_id = await questionary.text(
            "optional default chat_id for startup message (oc_xxx):",
            default="",
        ).ask_async()
        chat_id = chat_id.strip() if chat_id else None

        console.print("\nstep 2: agent cli tools")
        rows = _render_engine_table(console)
        installed_ids = [engine_id for engine_id, installed, _ in rows if installed]

        default_engine: str | None = None
        if installed_ids:
            default_engine = await questionary.select(
                "choose default agent:",
                choices=installed_ids,
            ).ask_async()
            if default_engine is None:
                return False
        else:
            console.print("no agents found on PATH. install one to continue.")
            save_anyway = await _confirm("save config anyway?", default=False)
            if not save_anyway:
                return False

        preview_config: dict[str, Any] = {}
        if default_engine is not None:
            preview_config["default_engine"] = default_engine
        preview_config["transport"] = "feishu"
        feishu_config: dict[str, Any] = {
            "app_id": mask_secret(app_id),
            "app_secret": mask_secret(app_secret),
        }
        if chat_id:
            feishu_config["chat_id"] = chat_id
        preview_config["transports"] = {"feishu": feishu_config}

        config_preview = dump_toml(preview_config).rstrip()
        console.print("\nstep 3: save configuration\n")
        console.print(f"  {_display_path(config_path)}\n")
        for line in config_preview.splitlines():
            console.print(f"  {line}")
        console.print("")

        save = await _confirm(
            f"save this config to {_display_path(config_path)}?",
            default=True,
        )
        if not save:
            return False

        raw_config: dict[str, Any] = {}
        if config_path.exists():
            try:
                raw_config = read_config(config_path)
            except ConfigError as exc:
                console.print(f"[yellow]warning:[/] config is malformed: {exc}")
                raw_config = {}

        merged = dict(raw_config)
        if default_engine is not None:
            merged["default_engine"] = default_engine
        merged["transport"] = "feishu"
        transports = ensure_table(merged, "transports", config_path=config_path)
        feishu_section = ensure_table(
            transports,
            "feishu",
            config_path=config_path,
            label="transports.feishu",
        )
        feishu_section["app_id"] = app_id
        feishu_section["app_secret"] = app_secret
        if chat_id:
            feishu_section["chat_id"] = chat_id

        write_config(merged, config_path)
        console.print(f"  config saved to {_display_path(config_path)}")

        done_panel = Panel(
            "setup complete. run 'takopi --transport feishu' to start!\n\n"
            "tip: enable long-connection event subscription in Feishu Open Platform.",
            border_style="green",
            padding=(1, 2),
            expand=False,
        )
        console.print("\n")
        console.print(done_panel)
        return True
