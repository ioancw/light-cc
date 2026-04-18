"""Tests for core/system_prompt.py build_system_prompt composition."""

from __future__ import annotations

import pytest

from core.models import SkillDef
from core.system_prompt import build_system_prompt
from skills.registry import register_skill, unregister_skill


@pytest.fixture
def ephemeral_skills():
    """Register a trio of skills and tear them down after the test.

    We rely on the process-wide registry; tearing the fixture down
    unregisters them so we don't leak state into sibling tests.
    """
    names = ["ephemeral-alpha", "ephemeral-beta", "ephemeral-gamma"]
    for n in names:
        register_skill(SkillDef(
            name=n,
            description=f"desc for {n}",
            user_invocable=True,
        ))
    yield names
    for n in names:
        unregister_skill(n)


class TestAllowedSkillsFilter:
    def test_none_lists_everything(self, ephemeral_skills):
        prompt = build_system_prompt(allowed_skills=None)
        assert "/ephemeral-alpha" in prompt
        assert "/ephemeral-beta" in prompt
        assert "/ephemeral-gamma" in prompt

    def test_narrows_to_allowlist(self, ephemeral_skills):
        prompt = build_system_prompt(allowed_skills=["ephemeral-alpha"])
        assert "/ephemeral-alpha" in prompt
        assert "/ephemeral-beta" not in prompt
        assert "/ephemeral-gamma" not in prompt

    def test_empty_list_hides_all_registered(self, ephemeral_skills):
        """An explicit empty allowlist should surface no registered skills in the Available Skills section."""
        prompt = build_system_prompt(allowed_skills=[])
        for n in ephemeral_skills:
            assert f"/{n}" not in prompt

    def test_namespaced_skill_matched_by_suffix(self):
        """A plugin-namespaced skill ``plug:name`` should match when the caller lists just ``name``."""
        register_skill(SkillDef(
            name="plug:ephemeral-nested",
            description="namespaced",
            user_invocable=True,
        ))
        try:
            prompt = build_system_prompt(allowed_skills=["ephemeral-nested"])
            assert "plug:ephemeral-nested" in prompt
        finally:
            unregister_skill("plug:ephemeral-nested")

    def test_unknown_allowlist_entry_is_silently_ignored(self, ephemeral_skills):
        prompt = build_system_prompt(allowed_skills=["not-a-real-skill"])
        for n in ephemeral_skills:
            assert f"/{n}" not in prompt
