"""Optional model routing -- classify input and select model.

Disabled by default. Enable via config: routing_enabled = true.
Rules are evaluated in order; first regex match wins.
No match falls back to the default model from settings.
"""

from __future__ import annotations

import logging
import re

from core.config import settings

logger = logging.getLogger(__name__)

_validated = False


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


def select_model(user_message: str) -> str:
    """Select model based on routing rules. Returns model ID.

    If routing is disabled or no rules match, returns settings.model.
    """
    if not settings.routing_enabled:
        return settings.model

    _validate_routing_rules()

    text = user_message.strip()

    for rule in settings.routing_rules:
        pattern = rule.get("pattern", "")
        model = rule.get("model", "")
        if pattern and model:
            try:
                if re.search(pattern, text, re.IGNORECASE):
                    logger.debug("Routing matched pattern %r -> %s", pattern, model)
                    return model
            except re.error:
                logger.warning("Invalid routing regex: %r", pattern)
                continue

    return settings.model
