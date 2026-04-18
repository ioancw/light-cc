"""Tests for core.router -- regex fast path, LLM classifier, fallbacks."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core import router
from core.config import settings


def _fake_response(label: str):
    """Mimic the Anthropic SDK's Message response shape (content is a list of blocks)."""
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=label)])


class TestOffMode:
    @pytest.mark.asyncio
    async def test_off_always_returns_default(self, monkeypatch):
        monkeypatch.setattr(settings, "routing_mode", "off")
        monkeypatch.setattr(settings, "model", "claude-sonnet-4-6")
        got = await router.select_model("plz refactor this whole codebase")
        assert got == "claude-sonnet-4-6"


class TestRegexFastPath:
    @pytest.mark.asyncio
    async def test_haiku_greeting_shortcuts_classifier(self, monkeypatch):
        monkeypatch.setattr(settings, "routing_mode", "llm")
        monkeypatch.setattr(settings, "routing_rules", [
            {"pattern": r"^(hi|hello)\b", "model": "claude-haiku-4-5-20251001"},
        ])
        # If the classifier were called, this would blow up -- proves fast path wins.
        with patch("core.router._llm_classify", new=AsyncMock(side_effect=AssertionError("classifier called"))):
            got = await router.select_model("hi there")
        assert got == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    async def test_no_regex_match_falls_through_in_regex_mode(self, monkeypatch):
        monkeypatch.setattr(settings, "routing_mode", "regex")
        monkeypatch.setattr(settings, "model", "claude-sonnet-4-6")
        monkeypatch.setattr(settings, "routing_rules", [
            {"pattern": r"^hi\b", "model": "claude-haiku-4-5-20251001"},
        ])
        got = await router.select_model("plot a sine wave for me")
        assert got == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_regex_mode_never_calls_classifier(self, monkeypatch):
        monkeypatch.setattr(settings, "routing_mode", "regex")
        monkeypatch.setattr(settings, "model", "claude-sonnet-4-6")
        monkeypatch.setattr(settings, "routing_rules", [])
        with patch("core.router._llm_classify", new=AsyncMock(side_effect=AssertionError("classifier called"))):
            got = await router.select_model("design a whole new architecture")
        assert got == "claude-sonnet-4-6"


class TestLLMClassifier:
    @pytest.mark.asyncio
    async def test_trivial_routes_to_haiku(self, monkeypatch):
        monkeypatch.setattr(settings, "routing_mode", "llm")
        monkeypatch.setattr(settings, "routing_rules", [])
        monkeypatch.setattr(settings, "routing_classifier_model", "claude-haiku-4-5-20251001")
        monkeypatch.setattr(settings, "routing_tier_models", {
            "TRIVIAL": "claude-haiku-4-5-20251001",
            "STANDARD": "claude-sonnet-4-6",
            "COMPLEX": "claude-opus-4-6",
        })
        mock_client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=_fake_response("TRIVIAL"))))
        with patch("core.client.get_client", return_value=mock_client):
            got = await router.select_model("what's 2 + 2?")
        assert got == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    async def test_standard_routes_to_sonnet(self, monkeypatch):
        monkeypatch.setattr(settings, "routing_mode", "llm")
        monkeypatch.setattr(settings, "routing_rules", [])
        monkeypatch.setattr(settings, "routing_tier_models", {
            "TRIVIAL": "claude-haiku-4-5-20251001",
            "STANDARD": "claude-sonnet-4-6",
            "COMPLEX": "claude-opus-4-6",
        })
        mock_client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=_fake_response("STANDARD"))))
        with patch("core.client.get_client", return_value=mock_client):
            got = await router.select_model("plot a sine wave for me")
        assert got == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_complex_routes_to_opus(self, monkeypatch):
        monkeypatch.setattr(settings, "routing_mode", "llm")
        monkeypatch.setattr(settings, "routing_rules", [])
        monkeypatch.setattr(settings, "routing_tier_models", {
            "TRIVIAL": "claude-haiku-4-5-20251001",
            "STANDARD": "claude-sonnet-4-6",
            "COMPLEX": "claude-opus-4-6",
        })
        mock_client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=_fake_response("COMPLEX"))))
        with patch("core.client.get_client", return_value=mock_client):
            got = await router.select_model("redesign the auth layer to support SSO across three tenants")
        assert got == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_label_with_trailing_punctuation_is_cleaned(self, monkeypatch):
        monkeypatch.setattr(settings, "routing_mode", "llm")
        monkeypatch.setattr(settings, "routing_rules", [])
        monkeypatch.setattr(settings, "routing_tier_models", {"STANDARD": "claude-sonnet-4-6"})
        mock_client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=_fake_response("STANDARD."))))
        with patch("core.client.get_client", return_value=mock_client):
            got = await router.select_model("anything")
        assert got == "claude-sonnet-4-6"


class TestLLMFallbacks:
    @pytest.mark.asyncio
    async def test_classifier_exception_falls_back_to_default(self, monkeypatch):
        monkeypatch.setattr(settings, "routing_mode", "llm")
        monkeypatch.setattr(settings, "routing_rules", [])
        monkeypatch.setattr(settings, "model", "claude-sonnet-4-6")
        mock_client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("boom"))))
        with patch("core.client.get_client", return_value=mock_client):
            got = await router.select_model("something unmatched")
        assert got == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_unknown_label_falls_back_to_default(self, monkeypatch):
        monkeypatch.setattr(settings, "routing_mode", "llm")
        monkeypatch.setattr(settings, "routing_rules", [])
        monkeypatch.setattr(settings, "model", "claude-sonnet-4-6")
        monkeypatch.setattr(settings, "routing_tier_models", {"STANDARD": "claude-sonnet-4-6"})
        mock_client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=_fake_response("MAYBE"))))
        with patch("core.client.get_client", return_value=mock_client):
            got = await router.select_model("anything")
        assert got == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_missing_client_falls_back_to_default(self, monkeypatch):
        monkeypatch.setattr(settings, "routing_mode", "llm")
        monkeypatch.setattr(settings, "routing_rules", [])
        monkeypatch.setattr(settings, "model", "claude-sonnet-4-6")
        with patch("core.client.get_client", side_effect=RuntimeError("no api key")):
            got = await router.select_model("anything")
        assert got == "claude-sonnet-4-6"


class TestClassifierCache:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        router._classifier_cache.clear()
        yield
        router._classifier_cache.clear()

    @pytest.mark.asyncio
    async def test_second_call_hits_cache_and_skips_classifier(self, monkeypatch):
        monkeypatch.setattr(settings, "routing_mode", "llm")
        monkeypatch.setattr(settings, "routing_rules", [])
        monkeypatch.setattr(settings, "routing_tier_models", {
            "STANDARD": "claude-sonnet-4-6",
        })
        create_mock = AsyncMock(return_value=_fake_response("STANDARD"))
        mock_client = SimpleNamespace(messages=SimpleNamespace(create=create_mock))
        with patch("core.client.get_client", return_value=mock_client):
            first = await router.select_model("plot a chart", user_id="u1", cid="c1")
            second = await router.select_model("now add a title", user_id="u1", cid="c1")
        assert first == "claude-sonnet-4-6"
        assert second == "claude-sonnet-4-6"
        # The classifier was called exactly once; second call came from cache.
        assert create_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_expired_cache_re_classifies(self, monkeypatch):
        monkeypatch.setattr(settings, "routing_mode", "llm")
        monkeypatch.setattr(settings, "routing_rules", [])
        monkeypatch.setattr(settings, "routing_tier_models", {
            "STANDARD": "claude-sonnet-4-6",
        })
        create_mock = AsyncMock(return_value=_fake_response("STANDARD"))
        mock_client = SimpleNamespace(messages=SimpleNamespace(create=create_mock))

        fake_now = [1000.0]
        monkeypatch.setattr(router.time, "time", lambda: fake_now[0])

        with patch("core.client.get_client", return_value=mock_client):
            await router.select_model("plot a chart", user_id="u1", cid="c1")
            # Jump past the TTL; the entry is now stale.
            fake_now[0] += router._CLASSIFIER_CACHE_TTL + 1
            await router.select_model("another request", user_id="u1", cid="c1")

        assert create_mock.await_count == 2


class TestConfigNormalization:
    def test_legacy_routing_enabled_maps_to_regex(self):
        from core.config import Settings
        s = Settings(routing_enabled=True, routing_mode="")
        assert s.routing_mode == "regex"

    def test_unset_mode_with_legacy_flag_maps_to_regex(self):
        from core.config import Settings
        s = Settings(routing_enabled=True)
        assert s.routing_mode == "regex"

    def test_unset_mode_without_legacy_flag_defaults_to_off(self):
        from core.config import Settings
        s = Settings()
        assert s.routing_mode == "off"

    def test_explicit_off_overrides_legacy_flag(self):
        from core.config import Settings
        s = Settings(routing_enabled=True, routing_mode="off")
        assert s.routing_mode == "off"

    def test_unknown_mode_defaults_to_off(self):
        from core.config import Settings
        s = Settings(routing_mode="banana")
        assert s.routing_mode == "off"
