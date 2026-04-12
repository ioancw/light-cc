"""Tests for webhook delivery (core/webhooks.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core import webhooks
from core.webhooks import deliver_webhook


def _mock_client(post_return=None, post_side_effect=None):
    """Build an httpx.AsyncClient mock whose async with block yields a configured client."""
    client = MagicMock()
    client.post = AsyncMock(return_value=post_return, side_effect=post_side_effect)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client


@pytest.fixture(autouse=True)
def _no_sleep():
    """Patch asyncio.sleep so retries don't actually wait."""
    with patch("core.webhooks.asyncio.sleep", new=AsyncMock()):
        yield


class TestDeliverWebhook:
    @pytest.mark.asyncio
    async def test_returns_false_for_empty_url(self):
        assert await deliver_webhook("", {"x": 1}) is False

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        resp = MagicMock(status_code=200, text="ok")
        cm, client = _mock_client(post_return=resp)

        with patch("core.webhooks.httpx.AsyncClient", return_value=cm):
            result = await deliver_webhook("https://example.com/hook", {"a": 1})

        assert result is True
        assert client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_success_after_retry(self):
        resp_fail = MagicMock(status_code=500, text="server error")
        resp_ok = MagicMock(status_code=200, text="ok")

        client = MagicMock()
        client.post = AsyncMock(side_effect=[resp_fail, resp_ok])
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("core.webhooks.httpx.AsyncClient", return_value=cm):
            result = await deliver_webhook("https://example.com/hook", {"a": 1})

        assert result is True
        assert client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_failure_after_max_retries(self):
        resp_fail = MagicMock(status_code=500, text="boom")
        cm, client = _mock_client(post_return=resp_fail)

        with patch("core.webhooks.httpx.AsyncClient", return_value=cm):
            result = await deliver_webhook(
                "https://example.com/hook", {"a": 1}, max_retries=3,
            )

        assert result is False
        assert client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_retries_on_exception(self):
        resp_ok = MagicMock(status_code=201, text="created")

        client = MagicMock()
        client.post = AsyncMock(side_effect=[
            httpx.ConnectError("fail 1"),
            resp_ok,
        ])
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("core.webhooks.httpx.AsyncClient", return_value=cm):
            result = await deliver_webhook(
                "https://example.com/hook", {"a": 1}, max_retries=3,
            )

        assert result is True
        assert client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_2xx_all_count_as_success(self):
        for code in (200, 201, 202, 204, 299):
            resp = MagicMock(status_code=code, text="ok")
            cm, client = _mock_client(post_return=resp)

            with patch("core.webhooks.httpx.AsyncClient", return_value=cm):
                result = await deliver_webhook(
                    "https://example.com/hook", {"a": 1},
                )
            assert result is True, f"status {code} should be success"

    @pytest.mark.asyncio
    async def test_non_2xx_triggers_retry(self):
        resp = MagicMock(status_code=404, text="not found")
        cm, client = _mock_client(post_return=resp)

        with patch("core.webhooks.httpx.AsyncClient", return_value=cm):
            result = await deliver_webhook(
                "https://example.com/hook", {"a": 1}, max_retries=2,
            )

        assert result is False
        assert client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_payload_posted_as_json(self):
        resp = MagicMock(status_code=200, text="ok")
        cm, client = _mock_client(post_return=resp)
        payload = {"agent_name": "x", "status": "completed"}

        with patch("core.webhooks.httpx.AsyncClient", return_value=cm):
            await deliver_webhook("https://example.com/hook", payload)

        call_kwargs = client.post.call_args.kwargs
        call_args = client.post.call_args.args
        assert call_args[0] == "https://example.com/hook"
        assert call_kwargs.get("json") == payload

    @pytest.mark.asyncio
    async def test_all_exceptions_returns_false(self):
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("core.webhooks.httpx.AsyncClient", return_value=cm):
            result = await deliver_webhook(
                "https://example.com/hook", {"a": 1}, max_retries=3,
            )

        assert result is False
        assert client.post.call_count == 3
