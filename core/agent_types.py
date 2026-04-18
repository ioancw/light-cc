"""Agent type definitions for sub-agent spawning.

Each agent type defines a system prompt, tool subset, and execution limits.
Sub-agents never receive the Agent tool itself (prevents recursive spawning).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentType:
    name: str
    system_prompt: str
    tool_names: list[str] = field(default_factory=list)  # empty = all (minus Agent tools)
    model: str | None = None  # None = inherit parent
    max_turns: int = 20
    timeout_seconds: int = 300
    max_result_chars: int = 10000


# Tools that sub-agents never receive (prevents recursive spawning).
# Covers the canonical Task name + all registered aliases.
EXCLUDED_TOOLS = {"Task", "Agent", "subagent", "BackgroundAgent", "background_agent", "AgentStatus"}

_registry: dict[str, AgentType] = {}


def register_agent_type(agent_type: AgentType) -> None:
    _registry[agent_type.name] = agent_type


def get_agent_type(name: str) -> AgentType | None:
    return _registry.get(name)


def list_agent_types() -> list[AgentType]:
    return list(_registry.values())


# ── Pre-defined types ──

register_agent_type(AgentType(
    name="explorer",
    system_prompt=(
        "You are an explorer agent. Your job is to search and read the codebase "
        "to answer questions or find information. You MUST NOT modify any files. "
        "Be thorough -- check multiple locations and naming conventions. "
        "Return a clear, concise summary of what you found."
    ),
    tool_names=["Read", "Grep", "Glob", "Bash", "WebFetch", "WebSearch", "ToolSearch"],
))

register_agent_type(AgentType(
    name="planner",
    system_prompt=(
        "You are a planning agent. Analyze the codebase and design an implementation "
        "approach for the task described. You MUST NOT modify any files. "
        "Consider existing patterns, identify critical files, and propose a concrete "
        "step-by-step plan. Note trade-offs between approaches."
    ),
    tool_names=["Read", "Grep", "Glob", "Bash"],
))

register_agent_type(AgentType(
    name="coder",
    system_prompt=(
        "You are a coding agent. Write, edit, and test code to complete the task. "
        "Follow existing patterns in the codebase. Be precise and minimal -- "
        "only change what is needed. Run tests or verification commands when appropriate."
    ),
    # Empty = all tools minus EXCLUDED_TOOLS
    tool_names=[],
))

register_agent_type(AgentType(
    name="researcher",
    system_prompt=(
        "You are a research agent. Search the web, fetch documentation, and analyze "
        "information to answer the research question. Synthesize findings into a clear, "
        "well-structured summary. Cite sources where relevant."
    ),
    tool_names=["WebFetch", "WebSearch", "Read", "Bash", "python_exec"],
))

register_agent_type(AgentType(
    name="default",
    system_prompt=(
        "You are a sub-agent working on a specific task. "
        "Complete the task and return a clear, concise result. "
        "You have access to the same tools as the parent agent."
    ),
    # Empty = all tools minus EXCLUDED_TOOLS
    tool_names=[],
))
