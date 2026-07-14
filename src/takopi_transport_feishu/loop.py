from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import anyio

from takopi.config import ConfigError
from takopi.context import RunContext
from takopi.directives import DirectiveError
from takopi.logging import bind_run_context, clear_context, get_logger
from takopi.model import ResumeToken
from takopi.runner_bridge import (
    ExecBridgeConfig,
    IncomingMessage,
    RunningTasks,
    handle_message,
)
from takopi.runners.run_options import EngineRunOptions, apply_run_options
from takopi.transport import MessageRef
from takopi.transport_runtime import TransportRuntime
from takopi.utils.paths import reset_run_base_dir, set_run_base_dir

from .access import is_chat_allowed, is_sender_allowed, should_handle_group_message
from .chat_prefs import FeishuChatPrefsStore, resolve_prefs_path
from .client import FeishuClient
from .command_handlers import (
    BUILTIN_CHAT_COMMANDS,
    engine_only_usage,
    handle_builtin_command,
    parse_slash_command,
    resolve_ambient_context,
    resolve_chat_engine,
)
from .diagnostics import run_startup_diagnostics
from .messages import FeishuIncomingMessage
from .overrides import resolve_overrides, resolve_trigger_mode
from .render import is_cancel_command
from .session import SessionStore
from .settings import FeishuTransportSettings
from .state import FeishuStateStore, resolve_state_path

logger = get_logger(__name__)

__all__ = ["FeishuBridgeConfig", "run_main_loop"]


@dataclass(frozen=True, slots=True)
class FeishuBridgeConfig:
    client: FeishuClient
    runtime: TransportRuntime
    settings: FeishuTransportSettings
    startup_msg: str
    exec_cfg: ExecBridgeConfig
    config_path: Path | None = None
    session_mode: Literal["stateless", "chat"] = "chat"
    show_resume_line: bool = True


async def _send_startup(cfg: FeishuBridgeConfig) -> None:
    chat_id = cfg.settings.chat_id
    if not chat_id:
        logger.info("startup.skipped", reason="no chat_id configured")
        return
    sent_id = await cfg.client.send_text(chat_id=chat_id, text=cfg.startup_msg)
    if sent_id:
        logger.info("startup.sent", chat_id=chat_id, message_id=sent_id)


async def _send_plain_reply(
    cfg: FeishuBridgeConfig,
    *,
    chat_id: str,
    reply_to_message_id: str,
    text: str,
    thread_id: str | None = None,
) -> None:
    await cfg.client.send_text(
        chat_id=chat_id,
        text=text,
        reply_to_message_id=reply_to_message_id,
        reply_in_thread=thread_id is not None,
    )


def _merge_context(
    directive_context: RunContext | None,
    ambient_context: RunContext | None,
) -> RunContext | None:
    if directive_context is not None:
        if (
            ambient_context is not None
            and directive_context.project is None
            and ambient_context.project is not None
        ):
            return RunContext(
                project=ambient_context.project,
                branch=directive_context.branch or ambient_context.branch,
            )
        return directive_context
    return ambient_context


async def run_main_loop(
    cfg: FeishuBridgeConfig,
    *,
    default_engine_override: str | None = None,
) -> None:
    running_tasks: RunningTasks = {}
    session_store = SessionStore()
    chat_prefs = (
        FeishuChatPrefsStore(resolve_prefs_path(cfg.config_path))
        if cfg.config_path is not None
        else None
    )
    state_store = (
        FeishuStateStore(resolve_state_path(cfg.config_path))
        if cfg.config_path is not None
        else None
    )

    cfg.client.start_ws()
    bot = await cfg.client.fetch_bot_identity()
    run_startup_diagnostics(cfg.client.api_client, cfg.settings)
    await _send_startup(cfg)

    logger.info(
        "feishu.ready",
        bot_name=bot.name,
        bot_open_id=bot.open_id,
        session_mode=cfg.session_mode,
    )

    async def cancel_for_chat(chat_id: str, thread_id: str | None) -> bool:
        for ref, task in list(running_tasks.items()):
            if str(ref.channel_id) != chat_id:
                continue
            if thread_id is not None and ref.thread_id != thread_id:
                continue
            task.cancel_requested.set()
            return True
        return False

    async def run_job(
        incoming: FeishuIncomingMessage,
        *,
        prompt: str,
        engine_id: str | None,
        resume_token: ResumeToken | None,
        context: RunContext | None,
    ) -> None:
        try:
            resolved = cfg.runtime.resolve_runner(
                resume_token=resume_token,
                engine_override=engine_id or default_engine_override,
            )
            if not resolved.available:
                await _send_plain_reply(
                    cfg,
                    chat_id=incoming.chat_id,
                    reply_to_message_id=incoming.message_id,
                    text=f"Engine unavailable: {resolved.issue or resolved.engine}",
                    thread_id=incoming.thread_id,
                )
                return

            try:
                cwd = cfg.runtime.resolve_run_cwd(context)
            except ConfigError as exc:
                await _send_plain_reply(
                    cfg,
                    chat_id=incoming.chat_id,
                    reply_to_message_id=incoming.message_id,
                    text=str(exc),
                    thread_id=incoming.thread_id,
                )
                return

            run_options: EngineRunOptions | None = None
            if chat_prefs is not None:
                overrides = await resolve_overrides(
                    chat_prefs,
                    chat_id=incoming.chat_id,
                    thread_id=incoming.thread_id,
                    engine_id=resolved.engine,
                )
                if overrides.model or overrides.reasoning:
                    run_options = EngineRunOptions(
                        model=overrides.model,
                        reasoning=overrides.reasoning,
                    )

            run_base_token = set_run_base_dir(cwd)
            try:
                bind_run_context(
                    chat_id=incoming.chat_id,
                    user_msg_id=incoming.message_id,
                    engine=resolved.runner.engine,
                    resume=resume_token.value if resume_token else None,
                )

                reply_ref = MessageRef(
                    channel_id=incoming.chat_id,
                    message_id=incoming.message_id,
                    thread_id=incoming.thread_id,
                )
                takopi_incoming = IncomingMessage(
                    channel_id=incoming.chat_id,
                    message_id=incoming.message_id,
                    text=prompt,
                    reply_to=reply_ref,
                    thread_id=incoming.thread_id,
                )
                context_line = cfg.runtime.format_context_line(context)

                async def on_thread_known(
                    token: ResumeToken, done: anyio.Event
                ) -> None:
                    del done
                    if cfg.session_mode == "chat":
                        session_store.set(
                            chat_id=incoming.chat_id,
                            thread_id=incoming.thread_id,
                            token=token,
                        )
                        logger.info(
                            "session.saved",
                            chat_id=incoming.chat_id,
                            thread_id=incoming.thread_id,
                            engine=token.engine,
                        )

                with apply_run_options(run_options):
                    await handle_message(
                        cfg.exec_cfg,
                        runner=resolved.runner,
                        incoming=takopi_incoming,
                        resume_token=resume_token,
                        context=context,
                        context_line=context_line,
                        strip_resume_line=cfg.runtime.is_resume_line,
                        running_tasks=running_tasks,
                        on_thread_known=on_thread_known
                        if cfg.session_mode == "chat"
                        else None,
                    )
            finally:
                reset_run_base_dir(run_base_token)
        except Exception:
            logger.exception(
                "run_job.failed",
                chat_id=incoming.chat_id,
                message_id=incoming.message_id,
            )
        finally:
            clear_context()

    async def handle_incoming(incoming: FeishuIncomingMessage) -> None:
        logger.info(
            "message.incoming",
            chat_id=incoming.chat_id,
            chat_type=incoming.chat_type,
            message_id=incoming.message_id,
            bot_mentioned=incoming.bot_mentioned,
            text_preview=incoming.text[:80],
        )
        if not is_chat_allowed(cfg.settings, incoming.chat_id):
            logger.info("message.skipped", reason="chat_not_allowed")
            return
        if not is_sender_allowed(cfg.settings, incoming.sender_open_id):
            logger.info("message.skipped", reason="sender_not_allowed")
            return

        text = incoming.text.strip()
        command_id, args_text = parse_slash_command(text)
        is_builtin_command = (
            command_id is not None and command_id in BUILTIN_CHAT_COMMANDS
        )

        if (
            chat_prefs is not None
            and incoming.thread_id is None
            and not is_builtin_command
        ):
            trigger_mode = await resolve_trigger_mode(
                chat_prefs,
                chat_id=incoming.chat_id,
                thread_id=None,
                default_mode=cfg.settings.trigger_mode_default,
            )
            if (
                trigger_mode == "mentions"
                and incoming.chat_type == "group"
                and not incoming.bot_mentioned
            ):
                logger.info("message.skipped", reason="trigger_mode_mentions")
                return

        if not is_builtin_command and not should_handle_group_message(
            cfg.settings,
            chat_type=incoming.chat_type,
            text=incoming.text,
            bot_name=bot.name,
            bot_mentioned=incoming.bot_mentioned,
        ):
            logger.info("message.skipped", reason="mention_required")
            return

        if not text:
            return

        if is_cancel_command(text):
            cancelled = await cancel_for_chat(incoming.chat_id, incoming.thread_id)
            if not cancelled:
                await _send_plain_reply(
                    cfg,
                    chat_id=incoming.chat_id,
                    reply_to_message_id=incoming.message_id,
                    text="No running task to cancel.",
                    thread_id=incoming.thread_id,
                )
            return

        if command_id is not None and command_id in BUILTIN_CHAT_COMMANDS:
            await handle_builtin_command(
                cfg,
                incoming,
                command_id,
                args_text,
                chat_prefs=chat_prefs,
                state_store=state_store,
                session_store=session_store,
                default_engine_override=default_engine_override,
                reply=lambda **kwargs: _send_plain_reply(
                    cfg,
                    chat_id=incoming.chat_id,
                    reply_to_message_id=incoming.message_id,
                    thread_id=incoming.thread_id,
                    **kwargs,
                ),
            )
            return

        ambient_context = await resolve_ambient_context(
            state_store,
            chat_id=incoming.chat_id,
            thread_id=incoming.thread_id,
        )

        try:
            resolved = cfg.runtime.resolve_message(
                text=text,
                reply_text=None,
                ambient_context=ambient_context,
                chat_id=None,
            )
        except DirectiveError as exc:
            await _send_plain_reply(
                cfg,
                chat_id=incoming.chat_id,
                reply_to_message_id=incoming.message_id,
                text=str(exc),
                thread_id=incoming.thread_id,
            )
            return

        prompt = resolved.prompt.strip()
        if not prompt:
            if resolved.engine_override is not None:
                await _send_plain_reply(
                    cfg,
                    chat_id=incoming.chat_id,
                    reply_to_message_id=incoming.message_id,
                    text=engine_only_usage(resolved.engine_override),
                    thread_id=incoming.thread_id,
                )
            return

        target_engine = await resolve_chat_engine(
            runtime=cfg.runtime,
            chat_prefs=chat_prefs,
            chat_id=incoming.chat_id,
            thread_id=incoming.thread_id,
            explicit_engine=resolved.engine_override,
            default_engine_override=default_engine_override,
            state_store=state_store,
        )

        resume_token: ResumeToken | None = resolved.resume_token
        if resume_token is None and cfg.session_mode == "chat":
            resume_token = session_store.get(
                chat_id=incoming.chat_id,
                thread_id=incoming.thread_id,
                engine=target_engine,
            )
        elif resume_token is not None and resume_token.engine != target_engine:
            resume_token = None

        context = _merge_context(resolved.context, ambient_context)

        await run_job(
            incoming,
            prompt=prompt,
            engine_id=resolved.engine_override or target_engine,
            resume_token=resume_token,
            context=context,
        )

    while True:
        messages = await cfg.client.poll_incoming(
            timeout_s=1.0,
            bot_open_id=bot.open_id,
        )
        for incoming in messages:
            await handle_incoming(incoming)
