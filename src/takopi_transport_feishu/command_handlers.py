from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from takopi.chat_help import ChatHelpOptions, format_chat_help
from takopi.config import ConfigError
from takopi.context import RunContext
from takopi.ids import RESERVED_CHAT_COMMANDS
from takopi.model import EngineId

from .chat_prefs import FeishuChatPrefsStore
from .command_utils import (
    normalize_branch_name,
    parse_set_args,
    resolve_target_scope,
    split_command_args,
)
from .file_transfer import (
    ZipTooLargeError,
    deny_reason,
    file_usage,
    format_bytes,
    normalize_relative_path,
    parse_file_command,
    resolve_path_within_root,
    save_bytes_to_path,
    zip_directory,
)
from .overrides import (
    REASONING_LEVELS,
    is_valid_reasoning_level,
    resolve_default_engine,
    resolve_overrides,
    resolve_trigger_mode,
    supports_reasoning,
)
from .session import SessionStore
from .state import FeishuStateStore
from .types import FeishuChatContext, FeishuThreadContext

if TYPE_CHECKING:
    from .loop import FeishuBridgeConfig
    from .messages import FeishuIncomingMessage

__all__ = [
    "AGENT_USAGE",
    "BUILTIN_CHAT_COMMANDS",
    "MODEL_USAGE",
    "REASONING_USAGE",
    "TRIGGER_USAGE",
    "CTX_USAGE",
    "TOPIC_USAGE",
    "engine_only_usage",
    "handle_builtin_command",
    "parse_slash_command",
    "resolve_ambient_context",
    "resolve_chat_engine",
]

BUILTIN_CHAT_COMMANDS = frozenset(RESERVED_CHAT_COMMANDS)
AGENT_USAGE = "usage: `/agent`, `/agent set <engine>`, or `/agent clear`"
MODEL_USAGE = (
    "usage: `/model`, `/model set <model>`, "
    "`/model set <engine> <model>`, or `/model clear [engine]`"
)
REASONING_USAGE = (
    "usage: `/reasoning`, `/reasoning set <level>`, "
    "`/reasoning set <engine> <level>`, or `/reasoning clear [engine]`"
)
TRIGGER_USAGE = "usage: `/trigger`, `/trigger set all|mentions`, or `/trigger clear`"
CTX_USAGE = "usage: `/ctx`, `/ctx set <project> [@branch]`, or `/ctx clear`"
TOPIC_USAGE = "usage: `/topic`, `/topic set @branch`, or `/topic clear` (thread only)"


def parse_slash_command(text: str) -> tuple[str | None, str]:
    stripped = text.lstrip()
    if not stripped.startswith("/"):
        return None, text
    lines = stripped.splitlines()
    if not lines:
        return None, text
    first_line = lines[0]
    token, _, rest = first_line.partition(" ")
    command = token[1:]
    if not command:
        return None, text
    if "@" in command:
        command = command.split("@", 1)[0]
    args_text = rest
    if len(lines) > 1:
        tail = "\n".join(lines[1:])
        args_text = f"{args_text}\n{tail}" if args_text else tail
    return command.lower(), args_text


def engine_only_usage(engine: EngineId) -> str:
    return (
        f"engine `{engine}` selected.\n"
        f"send a task after the prefix, e.g. `/{engine} fix the login bug`."
    )


async def resolve_ambient_context(
    state_store: FeishuStateStore | None,
    *,
    chat_id: str,
    thread_id: str | None,
) -> RunContext | None:
    if state_store is None:
        return None
    if thread_id is not None:
        thread_ctx = await state_store.get_context(chat_id, thread_id)
        if isinstance(thread_ctx, FeishuThreadContext):
            return RunContext(project=thread_ctx.project, branch=thread_ctx.branch)
    chat_ctx = await state_store.get_context(chat_id, None)
    if isinstance(chat_ctx, FeishuChatContext):
        return RunContext(project=chat_ctx.project, branch=chat_ctx.worktree_base)
    return None


async def resolve_chat_engine(
    *,
    runtime,
    chat_prefs: FeishuChatPrefsStore | None,
    chat_id: str,
    thread_id: str | None,
    explicit_engine: EngineId | None,
    default_engine_override: EngineId | None,
    state_store: FeishuStateStore | None = None,
) -> EngineId:
    if explicit_engine is not None:
        return explicit_engine
    if chat_prefs is not None:
        engine, _ = await resolve_default_engine(
            chat_prefs,
            chat_id=chat_id,
            thread_id=thread_id,
            config_default=None,
        )
        if engine is not None:
            return engine
    if state_store is not None and thread_id is not None:
        thread_ctx = await state_store.get_context(chat_id, thread_id)
        if isinstance(thread_ctx, FeishuThreadContext):
            return thread_ctx.default_engine
    if state_store is not None:
        chat_ctx = await state_store.get_context(chat_id, None)
        if isinstance(chat_ctx, FeishuChatContext):
            return chat_ctx.default_engine
    if default_engine_override is not None:
        return default_engine_override
    return runtime.default_engine


async def handle_builtin_command(
    cfg: FeishuBridgeConfig,
    incoming: FeishuIncomingMessage,
    command_id: str,
    args_text: str,
    *,
    chat_prefs: FeishuChatPrefsStore | None,
    state_store: FeishuStateStore | None,
    session_store: SessionStore,
    default_engine_override: EngineId | None,
    reply: Callable[..., Awaitable[None]],
) -> bool:
    handlers = {
        "help": _handle_help_command,
        "agent": _handle_agent_command,
        "model": _handle_model_command,
        "reasoning": _handle_reasoning_command,
        "trigger": _handle_trigger_command,
        "ctx": _handle_ctx_command,
        "topic": _handle_topic_command,
        "new": _handle_new_command,
        "file": _handle_file_command,
        "cancel": _handle_cancel_info_command,
    }
    handler = handlers.get(command_id)
    if handler is None:
        return False
    await handler(
        cfg,
        incoming,
        args_text,
        chat_prefs=chat_prefs,
        state_store=state_store,
        session_store=session_store,
        default_engine_override=default_engine_override,
        reply=reply,
    )
    return True


async def _handle_help_command(
    cfg: FeishuBridgeConfig,
    incoming: FeishuIncomingMessage,
    _args_text: str,
    *,
    reply: Callable[..., Awaitable[None]],
    **_kwargs,
) -> None:
    files = getattr(cfg, "files", None)
    text = format_chat_help(
        ChatHelpOptions(
            transport="feishu",
            engine_ids=tuple(cfg.runtime.engine_ids),
            project_aliases=tuple(cfg.runtime.project_aliases()),
            default_engine=cfg.runtime.default_engine,
            include_file=True,
            include_topics=False,
            files_enabled=bool(files and files.enabled),
        )
    )
    await reply(text=text)


async def _handle_cancel_info_command(
    *_args,
    reply: Callable[..., Awaitable[None]],
    **_kwargs,
) -> None:
    await reply(text="`/cancel` is handled by the message loop.")


async def _handle_agent_command(
    cfg: FeishuBridgeConfig,
    incoming: FeishuIncomingMessage,
    args_text: str,
    *,
    chat_prefs: FeishuChatPrefsStore | None,
    state_store: FeishuStateStore | None,
    default_engine_override: EngineId | None,
    reply: Callable[..., Awaitable[None]],
    **_kwargs,
) -> None:
    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "show"
    target_key, scope = resolve_target_scope(incoming.chat_id, incoming.thread_id)

    if action in {"show", ""}:
        current = await resolve_chat_engine(
            runtime=cfg.runtime,
            chat_prefs=chat_prefs,
            chat_id=incoming.chat_id,
            thread_id=incoming.thread_id,
            explicit_engine=None,
            default_engine_override=default_engine_override,
            state_store=state_store,
        )
        source = "global default"
        if chat_prefs is not None:
            _, src = await resolve_default_engine(
                chat_prefs,
                chat_id=incoming.chat_id,
                thread_id=incoming.thread_id,
                config_default=default_engine_override or cfg.runtime.default_engine,
            )
            if src:
                source = {
                    "thread": "thread override",
                    "chat": "chat override",
                    "config": "process default",
                }.get(src, source)
        available = ", ".join(cfg.runtime.engine_ids)
        overrides = None
        if chat_prefs is not None:
            overrides = await resolve_overrides(
                chat_prefs,
                chat_id=incoming.chat_id,
                thread_id=incoming.thread_id,
                engine_id=current,
            )
        lines = [
            f"engine: `{current}` ({source})",
            f"scope: `{scope}` (`{target_key}`)",
            f"available: `{available}`",
            AGENT_USAGE,
        ]
        if overrides and (overrides.model or overrides.reasoning):
            lines.append("overrides:")
            if overrides.model:
                lines.append(f"- model: `{overrides.model}` ({overrides.source_model})")
            if overrides.reasoning:
                lines.append(
                    f"- reasoning: `{overrides.reasoning}` ({overrides.source_reasoning})"
                )
        await reply(text="\n".join(lines))
        return

    if action == "set":
        if len(tokens) < 2:
            await reply(text=AGENT_USAGE)
            return
        engine = tokens[1].strip().lower()
        if engine not in cfg.runtime.engine_ids:
            await reply(
                text=f"unknown engine `{engine}`.\navailable: {', '.join(cfg.runtime.engine_ids)}",
            )
            return
        if chat_prefs is None:
            await reply(text="chat defaults are unavailable (no config path).")
            return
        await chat_prefs.set_default_engine(
            incoming.chat_id, engine, thread_id=incoming.thread_id
        )
        await reply(text=f"{scope} default engine set to `{engine}`")
        return

    if action == "clear":
        if chat_prefs is None:
            await reply(text="chat defaults are unavailable (no config path).")
            return
        await chat_prefs.clear_default_engine(
            incoming.chat_id, thread_id=incoming.thread_id
        )
        await reply(text=f"{scope} default engine cleared.")
        return

    await reply(text=AGENT_USAGE)


async def _handle_model_command(
    cfg: FeishuBridgeConfig,
    incoming: FeishuIncomingMessage,
    args_text: str,
    *,
    chat_prefs: FeishuChatPrefsStore | None,
    default_engine_override: EngineId | None,
    reply: Callable[..., Awaitable[None]],
    **_kwargs,
) -> None:
    if chat_prefs is None:
        await reply(text="model overrides are unavailable (no config path).")
        return
    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "show"
    engine_ids = {engine.lower() for engine in cfg.runtime.engine_ids}
    _, scope = resolve_target_scope(incoming.chat_id, incoming.thread_id)
    thread_id = incoming.thread_id

    if action in {"show", ""}:
        engine = await resolve_chat_engine(
            runtime=cfg.runtime,
            chat_prefs=chat_prefs,
            chat_id=incoming.chat_id,
            thread_id=thread_id,
            explicit_engine=None,
            default_engine_override=default_engine_override,
        )
        if len(tokens) >= 2 and tokens[1].lower() in engine_ids:
            engine = tokens[1].lower()
        overrides = await resolve_overrides(
            chat_prefs,
            chat_id=incoming.chat_id,
            thread_id=thread_id,
            engine_id=engine,
        )
        model_value = overrides.model or "default"
        source = overrides.source_model or "default"
        model_overrides, _, _, _ = await chat_prefs.get_all_overrides(
            incoming.chat_id, thread_id
        )
        if len(tokens) == 1 and model_overrides:
            lines = [f"**Model overrides ({scope})**"]
            for eng, mod in model_overrides.items():
                lines.append(f"- `{eng}`: `{mod}`")
            await reply(text="\n".join(lines))
            return
        await reply(
            text=(
                f"engine: `{engine}`\nmodel: `{model_value}` ({source})\n{MODEL_USAGE}"
            ),
        )
        return

    if action == "set":
        engine_arg, model = parse_set_args(tokens, engine_ids=engine_ids)
        if model is None:
            await reply(text=MODEL_USAGE)
            return
        engine = engine_arg or await resolve_chat_engine(
            runtime=cfg.runtime,
            chat_prefs=chat_prefs,
            chat_id=incoming.chat_id,
            thread_id=thread_id,
            explicit_engine=None,
            default_engine_override=default_engine_override,
        )
        await chat_prefs.set_model_override(incoming.chat_id, thread_id, engine, model)
        await reply(text=f"model override set for `{engine}`: `{model}` ({scope})")
        return

    if action == "clear":
        engine = None
        if len(tokens) >= 2 and tokens[1].lower() in engine_ids:
            engine = tokens[1].lower()
        if engine is None:
            model_overrides, _, _, _ = await chat_prefs.get_all_overrides(
                incoming.chat_id, thread_id
            )
            if model_overrides:
                for eng in list(model_overrides):
                    await chat_prefs.set_model_override(
                        incoming.chat_id, thread_id, eng, None
                    )
            await reply(text=f"all model overrides cleared ({scope}).")
            return
        await chat_prefs.set_model_override(incoming.chat_id, thread_id, engine, None)
        await reply(text=f"model override cleared for `{engine}` ({scope}).")
        return

    await reply(text=MODEL_USAGE)


async def _handle_reasoning_command(
    cfg: FeishuBridgeConfig,
    incoming: FeishuIncomingMessage,
    args_text: str,
    *,
    chat_prefs: FeishuChatPrefsStore | None,
    default_engine_override: EngineId | None,
    reply: Callable[..., Awaitable[None]],
    **_kwargs,
) -> None:
    if chat_prefs is None:
        await reply(text="reasoning overrides are unavailable (no config path).")
        return
    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "show"
    engine_ids = {engine.lower() for engine in cfg.runtime.engine_ids}
    _, scope = resolve_target_scope(incoming.chat_id, incoming.thread_id)
    thread_id = incoming.thread_id

    if action in {"show", ""}:
        _, reasoning_overrides, _, _ = await chat_prefs.get_all_overrides(
            incoming.chat_id, thread_id
        )
        if len(tokens) == 1:
            if not reasoning_overrides:
                await reply(text="No reasoning overrides set.")
                return
            lines = [f"**Reasoning overrides ({scope})**"]
            for eng, level in reasoning_overrides.items():
                lines.append(f"- `{eng}`: `{level}`")
            await reply(text="\n".join(lines))
            return
        if len(tokens) >= 2 and tokens[1].lower() in engine_ids:
            engine = tokens[1].lower()
            current = await chat_prefs.get_reasoning_override(
                incoming.chat_id, thread_id, engine
            )
            if current:
                await reply(text=f"reasoning override for `{engine}`: `{current}`")
            else:
                await reply(text=f"No reasoning override for `{engine}`.")
            return
        await reply(text=REASONING_USAGE)
        return

    if action == "set":
        engine_arg, level = parse_set_args(tokens, engine_ids=engine_ids)
        if level is None:
            await reply(text=REASONING_USAGE)
            return
        normalized = level.strip().lower()
        if normalized == "clear":
            engine = engine_arg or "codex"
            await chat_prefs.set_reasoning_override(
                incoming.chat_id, thread_id, engine, None
            )
            await reply(text=f"reasoning override cleared for `{engine}`.")
            return
        if not is_valid_reasoning_level(normalized):
            valid = ", ".join(sorted(REASONING_LEVELS))
            await reply(text=f"invalid reasoning level. valid: {valid}")
            return
        engine = engine_arg or "codex"
        if not supports_reasoning(engine):
            await reply(text=f"engine `{engine}` does not support reasoning overrides.")
            return
        await chat_prefs.set_reasoning_override(
            incoming.chat_id, thread_id, engine, normalized
        )
        await reply(
            text=f"reasoning override set for `{engine}`: `{normalized}` ({scope})"
        )
        return

    if action == "clear":
        engine = tokens[1].lower() if len(tokens) >= 2 else "codex"
        if engine not in engine_ids:
            await reply(text=REASONING_USAGE)
            return
        await chat_prefs.set_reasoning_override(
            incoming.chat_id, thread_id, engine, None
        )
        await reply(text=f"reasoning override cleared for `{engine}`.")
        return

    await reply(text=REASONING_USAGE)


async def _handle_trigger_command(
    cfg: FeishuBridgeConfig,
    incoming: FeishuIncomingMessage,
    args_text: str,
    *,
    chat_prefs: FeishuChatPrefsStore | None,
    reply: Callable[..., Awaitable[None]],
    **_kwargs,
) -> None:
    if chat_prefs is None:
        await reply(text="trigger settings are unavailable (no config path).")
        return
    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "show"
    _, scope = resolve_target_scope(incoming.chat_id, incoming.thread_id)
    thread_id = incoming.thread_id
    default_mode = cfg.settings.trigger_mode_default

    if action in {"show", ""}:
        current = await resolve_trigger_mode(
            chat_prefs,
            chat_id=incoming.chat_id,
            thread_id=thread_id,
            default_mode=default_mode,
        )
        stored = await chat_prefs.get_trigger_mode(incoming.chat_id, thread_id)
        if stored:
            await reply(
                text=f"trigger mode: `{current}` (set on this {scope})",
            )
        else:
            await reply(
                text=f"trigger mode: `{current}` (inherited/default)",
            )
        return

    if action == "set":
        if len(tokens) < 2:
            await reply(text=TRIGGER_USAGE)
            return
        mode = tokens[1].lower()
        if mode not in {"all", "mentions"}:
            await reply(text=TRIGGER_USAGE)
            return
        await chat_prefs.set_trigger_mode(incoming.chat_id, thread_id, mode)
        desc = (
            "respond to all messages"
            if mode == "all"
            else "only respond when @mentioned"
        )
        await reply(text=f"trigger mode set to `{mode}` ({desc}, {scope}).")
        return

    if action == "clear":
        await chat_prefs.set_trigger_mode(incoming.chat_id, thread_id, None)
        await reply(
            text=f"trigger mode cleared ({scope}); using default `{default_mode}`."
        )
        return

    await reply(text=TRIGGER_USAGE)


async def _handle_ctx_command(
    cfg: FeishuBridgeConfig,
    incoming: FeishuIncomingMessage,
    args_text: str,
    *,
    state_store: FeishuStateStore | None,
    reply: Callable[..., Awaitable[None]],
    **_kwargs,
) -> None:
    if state_store is None:
        await reply(text="context store is unavailable (no config path).")
        return
    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "show"
    thread_id = incoming.thread_id

    chat_ctx = await state_store.get_context(incoming.chat_id, None)
    thread_ctx = (
        await state_store.get_context(incoming.chat_id, thread_id)
        if thread_id
        else None
    )

    if action == "clear":
        target_thread = thread_id if thread_id is not None else None
        await state_store.clear_context(incoming.chat_id, target_thread)
        cleared = "thread" if target_thread else "chat"
        await reply(text=f"{cleared} context binding cleared.")
        return

    if action == "set":
        if thread_id is not None:
            branch = None
            project = None
            for token in tokens[1:]:
                if token.startswith("@"):
                    branch = normalize_branch_name(token)
                elif not project:
                    project = token.strip().lower()
            if project:
                await reply(
                    text=(
                        "In threads, `/ctx set` only supports rebinding the branch.\n"
                        "Use `/ctx set <project> [@branch]` in the parent chat."
                    ),
                )
                return
            if not branch:
                await reply(text="Usage (in thread): `/ctx set @branch-name`")
                return
            base = (
                thread_ctx if isinstance(thread_ctx, FeishuThreadContext) else chat_ctx
            )
            if base is None:
                await reply(
                    text="No project bound. Use `/ctx set <project>` in the chat first.",
                )
                return
            project_name = (
                base.project
                if isinstance(base, (FeishuChatContext, FeishuThreadContext))
                else None
            )
            if project_name is None:
                await reply(text=CTX_USAGE)
                return
            new_ctx = FeishuThreadContext(
                project=project_name,
                branch=branch,
                worktrees_dir=getattr(base, "worktrees_dir", ".worktrees"),
                default_engine=getattr(base, "default_engine", "claude"),
            )
            await state_store.set_context(incoming.chat_id, thread_id, new_ctx)
            await reply(
                text=(
                    "Thread context updated.\n"
                    f"- Project: `{new_ctx.project}`\n"
                    f"- Branch: `{new_ctx.branch}`"
                ),
            )
            return

        project = None
        branch = None
        for token in tokens[1:]:
            if token.startswith("@"):
                branch = normalize_branch_name(token)
            elif not project:
                project = token.strip().lower()
        if project is None and isinstance(chat_ctx, FeishuChatContext):
            project = chat_ctx.project
        if project is None:
            await reply(text=CTX_USAGE)
            return
        worktree_base = branch or (
            chat_ctx.worktree_base
            if isinstance(chat_ctx, FeishuChatContext)
            else "main"
        )
        normalized_project = cfg.runtime.normalize_project_key(project)
        if normalized_project is None:
            aliases = ", ".join(sorted(cfg.runtime.project_aliases()))
            await reply(text=f"unknown project `{project}`.\navailable: {aliases}")
            return
        project = normalized_project
        new_ctx = FeishuChatContext(
            project=project,
            worktrees_dir=(
                chat_ctx.worktrees_dir
                if isinstance(chat_ctx, FeishuChatContext)
                else ".worktrees"
            ),
            default_engine=(
                chat_ctx.default_engine
                if isinstance(chat_ctx, FeishuChatContext)
                else "claude"
            ),
            worktree_base=worktree_base,
        )
        await state_store.set_context(incoming.chat_id, None, new_ctx)
        await reply(
            text=(
                "Chat context updated.\n"
                f"- Project: `{new_ctx.project}`\n"
                f"- Base branch: `{new_ctx.worktree_base}`"
            ),
        )
        return

    lines = ["**Context**"]
    if isinstance(thread_ctx, FeishuThreadContext):
        lines.extend(
            [
                "**Resolved (thread)**",
                f"- Project: `{thread_ctx.project}`",
                f"- Branch: `{thread_ctx.branch}`",
                f"- Engine: `{thread_ctx.default_engine}`",
            ]
        )
    elif isinstance(chat_ctx, FeishuChatContext):
        lines.extend(
            [
                "**Resolved (chat)**",
                f"- Project: `{chat_ctx.project}`",
                f"- Base branch: `{chat_ctx.worktree_base}`",
                f"- Engine: `{chat_ctx.default_engine}`",
            ]
        )
    else:
        lines.append("No context bound. Use `/ctx set <project> [@branch]`.")
    await reply(text="\n".join(lines))


async def _handle_topic_command(
    cfg: FeishuBridgeConfig,
    incoming: FeishuIncomingMessage,
    args_text: str,
    *,
    state_store: FeishuStateStore | None,
    reply: Callable[..., Awaitable[None]],
    **_kwargs,
) -> None:
    if incoming.thread_id is None:
        await reply(
            text=(
                "`/topic` manages thread-level context.\n"
                "Send this command inside a Feishu thread, or use `/ctx` at chat level."
            ),
        )
        return
    await _handle_ctx_command(
        cfg,
        incoming,
        args_text,
        state_store=state_store,
        reply=reply,
    )


async def _handle_new_command(
    cfg: FeishuBridgeConfig,
    incoming: FeishuIncomingMessage,
    _args_text: str,
    *,
    session_store: SessionStore,
    reply: Callable[..., Awaitable[None]],
    **_kwargs,
) -> None:
    del cfg
    session_store.clear(
        chat_id=incoming.chat_id,
        thread_id=incoming.thread_id,
    )
    scope = "thread" if incoming.thread_id else "chat"
    await reply(text=f"Session cleared for this {scope}. Starting fresh.")


async def _resolve_project_root(
    cfg: FeishuBridgeConfig,
    incoming: FeishuIncomingMessage,
    *,
    state_store: FeishuStateStore | None,
) -> tuple[Path | None, RunContext | None]:
    if state_store is None:
        return None, None
    context = await resolve_ambient_context(
        state_store,
        chat_id=incoming.chat_id,
        thread_id=incoming.thread_id,
    )
    if context is None:
        return None, None
    try:
        root = cfg.runtime.resolve_run_cwd(context)
    except ConfigError:
        return None, None
    return root, context


async def _handle_file_command(
    cfg: FeishuBridgeConfig,
    incoming: FeishuIncomingMessage,
    args_text: str,
    *,
    state_store: FeishuStateStore | None,
    reply: Callable[..., Awaitable[None]],
    **_kwargs,
) -> None:
    files = cfg.settings.files
    if not files.enabled:
        await reply(
            text="file transfer disabled; enable `[transports.feishu.files]` in config.",
        )
        return

    command, path_text, error = parse_file_command(args_text)
    if error is not None or command is None:
        await reply(text=error or file_usage())
        return

    project_root, run_context = await _resolve_project_root(
        cfg, incoming, state_store=state_store
    )
    if project_root is None or run_context is None:
        await reply(
            text=(
                "This chat is not bound to a project.\n"
                "Use `/ctx set <project>` first to enable file transfers."
            ),
        )
        return

    deny_globs = files.deny_globs

    if command == "get":
        rel_path = normalize_relative_path(path_text)
        if rel_path is None:
            await reply(text="Invalid path. Must be relative, no `..` or `.git`.")
            return
        denied = deny_reason(rel_path, deny_globs)
        if denied:
            await reply(text=f"Path denied by rule: `{denied}`")
            return
        target = resolve_path_within_root(project_root, rel_path)
        if target is None:
            await reply(text="Path escapes project directory.")
            return
        if not target.exists():
            await reply(text=f"File not found: `{rel_path.as_posix()}`")
            return
        if target.is_dir():
            try:
                payload = zip_directory(
                    project_root,
                    rel_path,
                    deny_globs,
                    max_bytes=files.max_upload_bytes,
                )
                upload_name = f"{rel_path.name}.zip"
            except ZipTooLargeError:
                await reply(text="Directory zip exceeds size limit.")
                return
        else:
            payload = target.read_bytes()
            upload_name = target.name
            if len(payload) > files.max_upload_bytes:
                await reply(
                    text=(
                        f"File too large ({format_bytes(len(payload))}); "
                        f"limit is {format_bytes(files.max_upload_bytes)}."
                    ),
                )
                return
        sent = await cfg.client.send_file(
            chat_id=incoming.chat_id,
            filename=upload_name,
            payload=payload,
            reply_to_message_id=incoming.message_id,
            reply_in_thread=incoming.thread_id is not None,
        )
        if sent is None:
            await reply(text="Failed to send file.")
            return
        await reply(
            text=f"Sent `{upload_name}` ({format_bytes(len(payload))}) from `{rel_path.as_posix()}`.",
        )
        return

    force = "force" in split_command_args(path_text)
    path_tokens = [t for t in split_command_args(path_text) if t != "force"]
    rel_path = normalize_relative_path(" ".join(path_tokens))
    if rel_path is None:
        await reply(text="Invalid path for put.")
        return
    if incoming.parent_message_id is None:
        await reply(
            text=(
                "Reply to a file/image message with `/file put <path>` "
                "to upload into the project."
            ),
        )
        return
    downloaded = await cfg.client.download_message_file(incoming.parent_message_id)
    if downloaded is None:
        await reply(text="Could not download the replied-to file message.")
        return
    filename, payload = downloaded
    if rel_path.suffix == "" and filename:
        rel_path = rel_path / Path(filename).name
    result = save_bytes_to_path(
        payload,
        project_root,
        rel_path,
        deny_globs,
        max_bytes=files.max_upload_bytes,
        force=force,
    )
    if result.error:
        await reply(text=result.error)
        return
    assert result.rel_path is not None and result.size is not None
    await reply(
        text=(
            f"Saved `{result.rel_path.as_posix()}` ({format_bytes(result.size)})"
            + (" (overwritten)" if result.overwritten else "")
        ),
    )
