"""Tests for the Skill tool (tools/skill_tool.py).

The Skill tool lets an agent invoke a registered skill by name at runtime and
receive its body as guidance. This mirrors Claude Code's Skill tool.
"""

from __future__ import annotations

import json

import pytest

from core.models import SkillDef
from skills.registry import _SKILLS, register_skill
from tools.skill_tool import handle_skill


@pytest.fixture(autouse=True)
def clean_skills():
    original = dict(_SKILLS)
    _SKILLS.clear()
    yield
    _SKILLS.clear()
    _SKILLS.update(original)


def _make_skill(
    name: str,
    prompt: str = "Do the thing.",
    disable_model_invocation: bool = False,
) -> SkillDef:
    return SkillDef(
        name=name,
        description=f"Skill {name}",
        prompt=prompt,
        disable_model_invocation=disable_model_invocation,
    )


class TestSkillTool:
    @pytest.mark.asyncio
    async def test_returns_skill_body(self):
        register_skill(_make_skill("morning-briefing", prompt="Fetch BBC RSS and summarise."))
        result = await handle_skill({"skill": "morning-briefing"})
        assert "Fetch BBC RSS" in result
        # Should return the raw body, not a JSON-wrapped payload
        assert not result.startswith("{")

    @pytest.mark.asyncio
    async def test_resolves_arguments(self):
        register_skill(_make_skill(
            "analyze", prompt="Analyze ticker $ARGUMENTS for risks.",
        ))
        result = await handle_skill({"skill": "analyze", "args": "AAPL"})
        assert "Analyze ticker AAPL for risks." in result

    @pytest.mark.asyncio
    async def test_unknown_skill_returns_error(self):
        result = await handle_skill({"skill": "nonexistent"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Unknown skill" in parsed["error"]

    @pytest.mark.asyncio
    async def test_empty_name_returns_error(self):
        result = await handle_skill({"skill": ""})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "required" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_leading_slash_tolerated(self):
        register_skill(_make_skill("commit", prompt="Stage and commit changes."))
        result = await handle_skill({"skill": "/commit"})
        assert "Stage and commit" in result

    @pytest.mark.asyncio
    async def test_model_invocation_disabled_is_rejected(self):
        register_skill(_make_skill(
            "dangerous", prompt="...", disable_model_invocation=True,
        ))
        result = await handle_skill({"skill": "dangerous"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "cannot be invoked" in parsed["error"]


class TestRegistration:
    def test_skill_tool_is_registered(self):
        from tools.registry import _TOOLS
        assert "Skill" in _TOOLS
        handler, schema = _TOOLS["Skill"]
        assert schema["name"] == "Skill"
        assert "skill" in schema["input_schema"]["properties"]
        assert "args" in schema["input_schema"]["properties"]
        assert schema["input_schema"]["required"] == ["skill"]
