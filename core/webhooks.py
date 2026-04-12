"""Outbound webhook delivery with retry+backoff.

Used by the agent runner to notify external systems when an agent run completes.
Payload schema (JSON POST body):
    {
        "agent_id": str,
        "agent_name": str,
        "run_id": str,
        "status": "completed" | "failed",
        "trigger_type": str,
        "result_summary": str | null,
        "error": str | null,
        "tokens_used": int,
        "conversation_id": str | null,
        "timestamp": ISO8601
    }
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0  # seconds
MAX_RETRIES = 3
BASE_BACKOFF = 1.0  # seconds; doubles on each retry


async def deliver_webhook(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = MAX_RETRIES,
) -> bool:
    """POST the payload to url. Retries with exponential backoff on failure.

    Returns True on success (2xx response), False otherwise.
    """
    if not url:
        return False

    backoff = BASE_BACKOFF
    last_error: str | None = None

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload)
            if 200 <= resp.status_code < 300:
                logger.info(
                    f"Webhook delivered to {url} on attempt {attempt} (status={resp.status_code})"
                )
                return True
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            logger.warning(
                f"Webhook {url} attempt {attempt} returned {resp.status_code}: {resp.text[:200]}"
            )
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Webhook {url} attempt {attempt} raised: {e}")

        if attempt < max_retries:
            await asyncio.sleep(backoff)
            backoff *= 2

    logger.error(f"Webhook {url} failed after {max_retries} attempts: {last_error}")
    return False
