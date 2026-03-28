"""Anthropic SDK client singleton."""

from __future__ import annotations

import os

from anthropic import AsyncAnthropic

_client: AsyncAnthropic | None = None


def get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in environment or .env")
        _client = AsyncAnthropic(api_key=api_key)
    return _client
