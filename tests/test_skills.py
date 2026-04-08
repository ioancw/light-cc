"""Tests for skill registry and matching (skills/registry.py, core/models.py)."""

from __future__ import annotations

import pytest

from core.models import SkillDef
from skills.registry import (
    _SKILLS,
    get_skill,
    list_skills,
    match_skill_by_intent,
    match_skill_by_name,
    register_skill,
)


@pytest.fixture(autouse=True)
def clean_skills():
    """Clear the skill registry before and after each test."""
    original = dict(_SKILLS)
    _SKILLS.clear()
    yield
    _SKILLS.clear()
    _SKILLS.update(original)


def _make_skill(
    name: str = "test-skill",
    description: str = "A test skill",
    user_invocable: bool = True,
    disable_model_invocation: bool = False,
    tools: list[str] | None = None,
    prompt: str = "Do the thing: $ARGUMENTS",
    context: str = "",
) -> SkillDef:
    return SkillDef(
        name=name,
        description=description,
        user_invocable=user_invocable,
        disable_model_invocation=disable_model_invocation,
        tools=tools or [],
        prompt=prompt,
        context=context,
    )


class TestSkillRegistration:
    def test_register_and_get(self):
        skill = _make_skill(name="analyze")
        register_skill(skill)
        assert get_skill("analyze") is skill

    def test_get_nonexistent(self):
        assert get_skill("nonexistent") is None

    def test_list_skills(self):
        register_skill(_make_skill(name="a"))
        register_skill(_make_skill(name="b"))
        names = [s.name for s in list_skills()]
        assert "a" in names
        assert "b" in names


class TestMatchByName:
    def test_exact_match(self):
        skill = _make_skill(name="chart")
        register_skill(skill)
        assert match_skill_by_name("chart") is skill

    def test_no_match(self):
        assert match_skill_by_name("nonexistent") is None

    def test_non_invocable_not_matched(self):
        skill = _make_skill(name="internal", user_invocable=False)
        register_skill(skill)
        assert match_skill_by_name("internal") is None

    def test_namespaced_skill_matched_by_suffix(self):
        skill = _make_skill(name="my-plugin:chart")
        register_skill(skill)
        assert match_skill_by_name("chart") is skill

    def test_namespaced_skill_matched_exactly(self):
        skill = _make_skill(name="my-plugin:chart")
        register_skill(skill)
        assert match_skill_by_name("my-plugin:chart") is skill

    def test_prefers_exact_over_suffix(self):
        """If both 'chart' and 'plugin:chart' exist, exact match wins."""
        exact = _make_skill(name="chart")
        namespaced = _make_skill(name="plugin:chart")
        register_skill(exact)
        register_skill(namespaced)
        assert match_skill_by_name("chart") is exact


class TestMatchByIntent:
    def test_matches_skill_by_name_keywords(self):
        skill = _make_skill(name="data-analysis", description="Analyze datasets")
        register_skill(skill)
        # "data" + "analysis" in message -> score >= 2
        result = match_skill_by_intent("Can you do data analysis on this?")
        assert result is skill

    def test_no_match_below_threshold(self):
        skill = _make_skill(name="chart", description="Create visualizations")
        register_skill(skill)
        # "hello" has no keywords matching
        assert match_skill_by_intent("hello world") is None

    def test_disable_model_invocation_excluded(self):
        skill = _make_skill(
            name="data-analysis",
            description="Analyze data",
            disable_model_invocation=True,
        )
        register_skill(skill)
        assert match_skill_by_intent("data analysis please") is None

    def test_best_score_wins(self):
        low = _make_skill(name="chart", description="Make a chart")
        high = _make_skill(name="data-chart", description="Create data visualizations with charts")
        register_skill(low)
        register_skill(high)
        # "data chart" matches both name words of high -> higher score
        result = match_skill_by_intent("make a data chart")
        assert result is high


class TestSkillArgumentResolution:
    def test_arguments_substitution(self):
        skill = _make_skill(prompt="Analyze $ARGUMENTS")
        result = skill.resolve_arguments("AAPL")
        assert "Analyze AAPL" in result

    def test_positional_arguments(self):
        skill = _make_skill(prompt="Compare $ARGUMENTS[0] vs $ARGUMENTS[1]")
        result = skill.resolve_arguments("AAPL MSFT")
        assert "Compare AAPL vs MSFT" in result

    def test_shorthand_positional(self):
        skill = _make_skill(prompt="File: $0, Format: $1")
        result = skill.resolve_arguments("data.csv json")
        assert "File: data.csv" in result
        assert "Format: json" in result

    def test_session_id_substitution(self):
        skill = _make_skill(prompt="Session: ${CLAUDE_SESSION_ID}")
        result = skill.resolve_arguments("", session_id="abc-123")
        assert "Session: abc-123" in result

    def test_skill_dir_substitution(self):
        skill = SkillDef(
            name="test",
            prompt="Dir: ${CLAUDE_SKILL_DIR}",
            skill_dir="/skills/test",
        )
        result = skill.resolve_arguments("")
        assert "Dir: /skills/test" in result

    def test_arguments_appended_if_not_referenced(self):
        skill = _make_skill(prompt="Do something cool")
        result = skill.resolve_arguments("with these args")
        assert "ARGUMENTS: with these args" in result

    def test_empty_args_not_appended(self):
        skill = _make_skill(prompt="Do something cool")
        result = skill.resolve_arguments("")
        assert "ARGUMENTS:" not in result

    def test_missing_positional_arg_empty_string(self):
        skill = _make_skill(prompt="A: $ARGUMENTS[0], B: $ARGUMENTS[1]")
        result = skill.resolve_arguments("only_one")
        assert "A: only_one" in result
        # Missing $ARGUMENTS[1] resolves to empty string, so "B: " becomes "B:"
        assert "B:" in result


class TestToolFilter:
    def test_skill_with_tools(self):
        skill = _make_skill(tools=["Read", "Glob", "Grep"])
        assert skill.tools == ["Read", "Glob", "Grep"]

    def test_skill_without_tools(self):
        skill = _make_skill()
        assert skill.tools == []
