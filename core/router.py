"""Model routing -- classify input and select the model to handle the turn.

Modes (settings.routing_mode):
  "off"   -- always return settings.model
  "regex" -- first regex match in settings.routing_rules wins
  "llm"   -- regex runs first as a fast path; if no rule matches, a small
             classifier model labels the message TRIVIAL/STANDARD/COMPLEX and
             we map that to settings.routing_tier_models.

Public entry point: `select_model` (async).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from core.config import settings

logger = logging.getLogger(__name__)

_validated = False

# Per-conversation classifier decision cache.
# Key: (user_id, cid). Value: (model_id, expires_at_epoch).
# Skipping the classifier for follow-up turns in the same conversation saves a
# round-trip + ~100ms per turn. TTL is short so a shift in topic re-classifies.
_classifier_cache: dict[tuple[str, str], tuple[str, float]] = {}
_CLASSIFIER_CACHE_TTL = 600.0  # 10 minutes


def _cache_get(user_id: str | None, cid: str | None) -> str | None:
    if not user_id or not cid:
        return None
    entry = _classifier_cache.get((user_id, cid))
    if not entry:
        return None
    model, expires_at = entry
    if time.time() >= expires_at:
        _classifier_cache.pop((user_id, cid), None)
        return None
    return model


def _cache_put(user_id: str | None, cid: str | None, model: str) -> None:
    if not user_id or not cid:
        return
    _classifier_cache[(user_id, cid)] = (model, time.time() + _CLASSIFIER_CACHE_TTL)

_CLASSIFIER_SYSTEM = (
    "You are a model router. Classify the user's message into exactly one tier:\n"
    "\n"
    "TRIVIAL  -- greetings, thanks, yes/no acknowledgements, single-fact lookups, "
    "trivial arithmetic, status checks. No tools needed or only one read-only tool.\n"
    "STANDARD -- ordinary coding tasks, debugging a bounded problem, writing a short "
    "script, making a chart from given data, answering a focused question, small edits. "
    "Default bucket for most requests.\n"
    "COMPLEX  -- multi-step refactors spanning many files, architectural design, deep "
    "research/literature review, large codebase audits, novel algorithmic problems, "
    "whole-feature implementations.\n"
    "\n"
    "Reply with EXACTLY ONE WORD: TRIVIAL, STANDARD, or COMPLEX. No punctuation, no explanation."
)

_VALID_TIERS = {"TRIVIAL", "STANDARD", "COMPLEX"}


def _validate_routing_rules() -> None:
    """Warn once at first use if routing rules reference unknown models."""
    global _validated
    if _validated:
        return
    _validated = True

    known = set(settings.available_models)
    known.add(settings.model)
    for rule in settings.routing_rules:
        model = rule.get("model", "")
        if model and model not in known:
            logger.warning(
                "Routing rule references model %r which is not in available_models: %s",
                model, sorted(known),
            )


def _regex_match(text: str) -> str | None:
    """Return the model from the first matching regex rule, or None."""
    for rule in settings.routing_rules:
        pattern = rule.get("pattern", "")
        model = rule.get("model", "")
        if not (pattern and model):
            continue
        try:
            if re.search(pattern, text, re.IGNORECASE):
                logger.debug("Routing matched regex %r -> %s", pattern, model)
                return model
        except re.error:
            logger.warning("Invalid routing regex: %r", pattern)
            continue
    return None


async def _llm_classify(text: str) -> str | None:
    """Ask the classifier model to label the message. Returns a model id or None on failure."""
    try:
        from core.client import get_client
        client = get_client()
    except Exception as e:
        logger.warning("LLM routing unavailable (no client): %s", e)
        return None

    # Keep the classifier prompt tiny and bounded.
    excerpt = text.strip()
    if len(excerpt) > 1200:
        excerpt = excerpt[:1200] + " [...]"

    try:
        resp = await asyncio.wait_for(
            client.messages.create(
                model=settings.routing_classifier_model,
                max_tokens=8,
                system=_CLASSIFIER_SYSTEM,
                messages=[{"role": "user", "content": excerpt}],
            ),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        logger.warning("LLM router timed out; falling back to default model")
        return None
    except Exception as e:
        logger.warning("LLM router call failed: %s", e)
        return None

    label = ""
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", "") == "text":
            label = (getattr(block, "text", "") or "").strip().upper()
            break
    # Accept first token if the model added trailing punctuation.
    label = re.split(r"[^A-Z]", label, maxsplit=1)[0] if label else ""

    if label not in _VALID_TIERS:
        logger.warning("LLM router returned unexpected label %r; using default", label)
        return None

    model = settings.routing_tier_models.get(label)
    if not model:
        logger.warning("No model mapped for tier %r; using default", label)
        return None

    logger.info("LLM router: %r -> %s", label, model)
    return model


async def select_model(
    user_message: str,
    *,
    user_id: str | None = None,
    cid: str | None = None,
) -> str:
    """Select a model for the given user message.

    Async so it can call the classifier when routing_mode='llm'. When
    ``user_id`` and ``cid`` are provided, classifier decisions are cached
    per-conversation with a short TTL, so follow-up turns skip the round-trip.
    """
    mode = settings.routing_mode
    if mode == "off":
        logger.info("routing_decision mode=off -> %s", settings.model)
        return settings.model

    _validate_routing_rules()
    text = user_message.strip()

    # Regex fast path runs in both "regex" and "llm" modes.
    matched = _regex_match(text)
    if matched is not None:
        logger.info("routing_decision mode=%s via=regex -> %s", mode, matched)
        return matched

    if mode == "llm":
        cached = _cache_get(user_id, cid)
        if cached is not None:
            logger.info("routing_decision mode=llm via=cache -> %s", cached)
            return cached
        picked = await _llm_classify(text)
        if picked is not None:
            _cache_put(user_id, cid, picked)
            logger.info("routing_decision mode=llm via=classifier -> %s", picked)
            return picked

    logger.info("routing_decision mode=%s via=default -> %s", mode, settings.model)
    return settings.model
