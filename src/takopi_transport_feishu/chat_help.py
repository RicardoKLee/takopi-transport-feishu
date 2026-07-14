from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChatHelpOptions:
    transport: str
    engine_ids: tuple[str, ...]
    project_aliases: tuple[str, ...]
    default_engine: str
    include_file: bool = False
    include_topics: bool = False
    files_enabled: bool = False


def format_chat_help(options: ChatHelpOptions) -> str:
    engines = ", ".join(f"`{e}`" for e in options.engine_ids) or "`none`"
    projects = ", ".join(f"`{a}`" for a in options.project_aliases) or "`none`"

    lines = [
        f"**Takopi help** ({options.transport})",
        "",
        "**Quick start**",
        "- Send a task directly, or prefix with an engine: `/qoder fix the bug`",
        f"- Default engine: `{options.default_engine}`",
        f"- Available engines: {engines}",
        f"- Project aliases: {projects}",
        "",
        "**Chat commands**",
        "- `/help` — show this guide",
        "- `/agent` — show default engine; `/agent set <engine>`; `/agent clear`",
        "- `/model` — show model overrides; `/model set [engine] <model>`; `/model clear`",
        "- `/reasoning` — show reasoning overrides (codex); `/reasoning set [engine] <level>`",
        "- `/trigger` — show trigger mode; `/trigger set all|mentions`; `/trigger clear`",
        "- `/ctx` — show project binding; `/ctx set <project> [@branch]`; `/ctx clear`",
        "- `/new` — clear conversation session and start fresh",
        "- `/cancel` — cancel the current running task",
    ]

    if options.include_topics:
        lines.append(
            "- `/topic <issue title>` — create a forum topic for one issue/task"
        )
    else:
        lines.append(
            "- `/topic <issue title>` — thread issue binding (inside a thread)"
        )

    if options.include_file:
        if options.files_enabled:
            lines.extend(
                [
                    "- `/file get <path>` — download a project file",
                    "- `/file put <path>` — upload (reply to a file message first on Feishu)",
                ]
            )
        else:
            lines.append(
                "- `/file` — file transfer (enable `[transports."
                f"{options.transport}.files]` in config)"
            )

    lines.extend(
        [
            "",
            "**Directives in messages**",
            "- `/<engine> your task` — one-shot engine selection",
            "- `/<project> your task` — run in a project context",
            "- `@branch your task` — run on a branch/worktree",
            "",
            "**Examples**",
            "- `/agent set qoder` then `explain this repo`",
            "- `/ctx set sandbox @main` then `/qoder add tests`",
            "- `/model set cursor auto`",
        ]
    )

    if options.transport == "feishu":
        lines.append("- In groups: `@bot your task` when mention mode is on")
    elif options.transport == "telegram":
        lines.append("- In groups: `@BotName your task` or reply to the bot")
    elif options.transport == "discord":
        lines.append("- In servers: use slash commands or @mention the bot")

    return "\n".join(lines)
