"""Probe the Claude Agent SDK to capture its event contracts.

Not wired into Light CC. Runs a matrix of scenarios through the SDK and
dumps each event stream to data/sdk_traces/<scenario>.json so the shapes
can be read off and ported into Light CC (core/agent.py, core/hooks.py,
core/permission_modes.py, the Task subagent contract, etc.).

Requirements:
    pip install claude-agent-sdk anyio
    Claude Code CLI installed on PATH (the SDK spawns it as a subprocess)
    ANTHROPIC_API_KEY set, or existing `claude` auth

Run all scenarios:
    python scripts/sdk_probe.py

Run one:
    python scripts/sdk_probe.py hooks_fire
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    CLINotFoundError,
    HookMatcher,
    TextBlock,
    ToolUseBlock,
    query,
)

TRACE_DIR = Path(__file__).resolve().parent.parent / "data" / "sdk_traces"


def encode(obj: Any) -> Any:
    if is_dataclass(obj):
        return {"__type__": type(obj).__name__, **asdict(obj)}
    if hasattr(obj, "__dict__"):
        return {"__type__": type(obj).__name__, **{k: encode(v) for k, v in obj.__dict__.items()}}
    if isinstance(obj, list):
        return [encode(x) for x in obj]
    if isinstance(obj, dict):
        return {k: encode(v) for k, v in obj.items()}
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return repr(obj)


@dataclass
class Scenario:
    name: str
    prompt: str
    system_prompt: str
    allowed_tools: list[str]
    permission_mode: str = "bypassPermissions"
    max_turns: int = 4
    hook_events: list[str] = field(default_factory=list)
    note: str = ""


def make_hook(event_name: str, trace: list[dict]) -> Callable[..., Awaitable[dict]]:
    async def _hook(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        trace.append({
            "__type__": "HookFire",
            "event": event_name,
            "tool_use_id": tool_use_id,
            "input_data": encode(input_data),
        })
        return {}
    return _hook


async def run_scenario(sc: Scenario) -> None:
    print(f"\n### {sc.name} ###")
    if sc.note:
        print(f"# {sc.note}")

    trace: list[dict] = []

    hooks_cfg: dict[str, list[HookMatcher]] = {}
    for event in sc.hook_events:
        hooks_cfg[event] = [HookMatcher(hooks=[make_hook(event, trace)])]

    options = ClaudeAgentOptions(
        system_prompt=sc.system_prompt,
        allowed_tools=sc.allowed_tools,
        permission_mode=sc.permission_mode,
        max_turns=sc.max_turns,
        hooks=hooks_cfg or None,
    )

    try:
        async for message in query(prompt=sc.prompt, options=options):
            trace.append(encode(message))
            label = type(message).__name__
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(f"  {label}.TextBlock {block.text[:80]!r}")
                    elif isinstance(block, ToolUseBlock):
                        print(f"  {label}.ToolUseBlock name={block.name}")
                    else:
                        print(f"  {label}.{type(block).__name__}")
            else:
                print(f"  {label}")
    except CLINotFoundError as e:
        trace.append({"__type__": "Error", "error": "CLINotFoundError", "detail": str(e)})
        print(f"  ERROR: Claude Code CLI not found — {e}")
    except Exception as e:
        trace.append({"__type__": "Error", "error": type(e).__name__, "detail": str(e)})
        print(f"  ERROR: {type(e).__name__}: {e}")

    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    out = TRACE_DIR / f"{sc.name}.json"
    out.write_text(json.dumps({
        "scenario": asdict(sc),
        "events": trace,
    }, indent=2, default=str))
    print(f"  -> {out}")


SCENARIOS: list[Scenario] = [
    Scenario(
        name="tools_basic",
        prompt="Run `echo hello`, then read the first line of README.md if it exists.",
        system_prompt="You are terse. Use tools when asked.",
        allowed_tools=["Bash", "Read", "Glob"],
        note="Shape of ToolUseBlock/ToolResultBlock across a few tool types.",
    ),
    Scenario(
        name="hooks_fire",
        prompt="Run `echo hook-probe` via Bash.",
        system_prompt="You are terse.",
        allowed_tools=["Bash"],
        hook_events=[
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "Stop",
            "SessionStart",
        ],
        note="Hook payload shape for each event the SDK exposes.",
    ),
    Scenario(
        name="subagent_spawn",
        prompt="Use the Task tool to spawn a general-purpose subagent whose job is to run `pwd` and report the result.",
        system_prompt="You are terse. Prefer delegating to subagents via the Task tool.",
        allowed_tools=["Task", "Bash"],
        max_turns=6,
        note="Task tool spawn + return contract — how the parent sees the subagent's summary.",
    ),
    Scenario(
        name="permission_plan",
        prompt="Add a comment to README.md saying 'probe'.",
        system_prompt="You are terse.",
        allowed_tools=["Read", "Edit", "Write"],
        permission_mode="plan",
        note="Plan mode should refuse writes and emit an ExitPlanMode proposal.",
    ),
    Scenario(
        name="permission_accept_edits",
        prompt="Create a file /tmp/sdk_probe_marker.txt with the text 'ok'.",
        system_prompt="You are terse.",
        allowed_tools=["Write"],
        permission_mode="acceptEdits",
        note="acceptEdits mode: edits go through without prompt, other tools do not.",
    ),
]


async def main() -> None:
    wanted = sys.argv[1:] if len(sys.argv) > 1 else None
    for sc in SCENARIOS:
        if wanted and sc.name not in wanted:
            continue
        await run_scenario(sc)


if __name__ == "__main__":
    anyio.run(main)
