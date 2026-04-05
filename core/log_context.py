"""Structured logging with correlation IDs from session context vars."""

from __future__ import annotations

import logging

from core.config import settings
from core.session import _current_session_id, _current_cid


class ContextFilter(logging.Filter):
    """Inject session_id and cid into every log record."""

    def filter(self, record):
        record.session_id = _current_session_id.get("") or "-"
        record.cid = _current_cid.get("") or "-"
        return True


def setup_logging() -> None:
    """Configure the root logger with correlation-ID formatting."""
    fmt = "%(asctime)s %(levelname)s [%(session_id)s:%(cid)s] %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    ctx_filter = ContextFilter()

    root = logging.getLogger()
    # Add filter to the handler (not the logger) so ALL records passing
    # through get session_id/cid injected — including those from child
    # loggers, uvicorn, and multiprocessing that propagate up.
    handler.addFilter(ctx_filter)
    root.addHandler(handler)
    root.setLevel(getattr(settings, "log_level", "INFO"))
