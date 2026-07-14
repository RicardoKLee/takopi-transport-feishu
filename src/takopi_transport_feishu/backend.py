from __future__ import annotations

import os
from pathlib import Path

import anyio

from takopi.backends import EngineBackend
from takopi.logging import get_logger
from takopi.runner_bridge import ExecBridgeConfig
from takopi.transport_runtime import TransportRuntime
from takopi.transports import SetupResult, TransportBackend

from .client import FeishuClient
from .loop import FeishuBridgeConfig, run_main_loop
from .onboarding import check_setup, interactive_setup
from .presenter import FeishuPresenter
from .settings import FeishuTransportSettings, parse_feishu_settings
from .transport import FeishuTransport

logger = get_logger(__name__)

__all__ = ["BACKEND", "FeishuBackend"]


def _get_feishu_settings(transport_config: object) -> FeishuTransportSettings:
    if isinstance(transport_config, FeishuTransportSettings):
        return transport_config
    if isinstance(transport_config, dict):
        return parse_feishu_settings(transport_config)
    if hasattr(transport_config, "model_dump"):
        raw = transport_config.model_dump()
        if isinstance(raw, dict):
            return parse_feishu_settings(raw)
    raise TypeError(f"unexpected transport_config type: {type(transport_config)}")


def _build_startup_message(
    runtime: TransportRuntime,
    *,
    startup_pwd: str,
    session_mode: str,
    show_resume_line: bool,
) -> str:
    available_engines = list(runtime.available_engine_ids())
    missing_engines = list(runtime.missing_engine_ids())
    misconfigured_engines = list(runtime.engine_ids_with_status("bad_config"))
    failed_engines = list(runtime.engine_ids_with_status("load_error"))

    engine_list = ", ".join(available_engines) if available_engines else "none"
    notes: list[str] = []
    if missing_engines:
        notes.append(f"not installed: {', '.join(missing_engines)}")
    if misconfigured_engines:
        notes.append(f"misconfigured: {', '.join(misconfigured_engines)}")
    if failed_engines:
        notes.append(f"failed to load: {', '.join(failed_engines)}")
    if notes:
        engine_list = f"{engine_list} ({'; '.join(notes)})"

    project_aliases = sorted(set(runtime.project_aliases()), key=str.lower)
    project_list = ", ".join(project_aliases) if project_aliases else "none"
    resume_label = "shown" if show_resume_line else "hidden"

    return (
        f"🐙 **takopi-feishu is ready**\n\n"
        f"default: `{runtime.default_engine}`  \n"
        f"engines: `{engine_list}`  \n"
        f"projects: `{project_list}`  \n"
        f"mode: `{session_mode}`  \n"
        f"resume lines: `{resume_label}`  \n"
        f"working in: `{startup_pwd}`"
    )


class FeishuBackend(TransportBackend):
    id = "feishu"
    description = "Feishu/Lark bot"

    def check_setup(
        self,
        engine_backend: EngineBackend,
        *,
        transport_override: str | None = None,
    ) -> SetupResult:
        return check_setup(engine_backend, transport_override=transport_override)

    async def interactive_setup(self, *, force: bool) -> bool:
        return await interactive_setup(force=force)

    def lock_token(self, *, transport_config: object, _config_path: Path) -> str | None:
        settings = _get_feishu_settings(transport_config)
        return f"{settings.app_id}:{settings.app_secret}"

    def build_and_run(
        self,
        *,
        transport_config: object,
        config_path: Path,
        runtime: TransportRuntime,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None:
        settings = _get_feishu_settings(transport_config)
        client = FeishuClient(settings)
        transport = FeishuTransport(client)
        presenter = FeishuPresenter(message_overflow=settings.message_overflow)
        exec_cfg = ExecBridgeConfig(
            transport=transport,
            presenter=presenter,
            final_notify=final_notify,
        )
        startup_msg = _build_startup_message(
            runtime,
            startup_pwd=os.getcwd(),
            session_mode=settings.session_mode,
            show_resume_line=settings.show_resume_line,
        )
        cfg = FeishuBridgeConfig(
            client=client,
            runtime=runtime,
            settings=settings,
            startup_msg=startup_msg,
            exec_cfg=exec_cfg,
            config_path=config_path,
            session_mode=settings.session_mode,
            show_resume_line=settings.show_resume_line,
        )

        async def run_loop() -> None:
            await run_main_loop(
                cfg,
                default_engine_override=default_engine_override,
            )

        anyio.run(run_loop)


feishu_backend = FeishuBackend()
BACKEND = feishu_backend
