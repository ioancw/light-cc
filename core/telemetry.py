"""Observability: structured logging, OpenTelemetry tracing, Prometheus metrics.

Call `setup_telemetry()` once at app startup. All three subsystems are optional
and degrade gracefully if their dependencies are missing.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator

logger = logging.getLogger(__name__)

# ── Structured Logging (structlog) ───────────────────────────────────

def setup_structured_logging() -> None:
    """Configure structlog for JSON output in prod, pretty console in dev."""
    try:
        import structlog

        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.StackInfoRenderer(),
                structlog.dev.set_exc_info,
                structlog.processors.TimeStamper(fmt="iso"),
                # Use JSON in prod (when DEBUG is off), pretty console otherwise
                structlog.dev.ConsoleRenderer()
                if logging.root.level <= logging.DEBUG
                else structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
        logger.info("Structured logging configured (structlog)")
    except ImportError:
        logger.info("structlog not installed — using stdlib logging")


def get_logger(name: str) -> Any:
    """Get a structured logger (falls back to stdlib if structlog unavailable)."""
    try:
        import structlog
        return structlog.get_logger(name)
    except ImportError:
        return logging.getLogger(name)


# ── OpenTelemetry Tracing ────────────────────────────────────────────

_tracer = None


def setup_tracing(service_name: str = "light-cc") -> None:
    """Initialize OpenTelemetry tracing with OTLP exporter."""
    global _tracer
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        # Try OTLP exporter (sends to Jaeger/Tempo/etc.)
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        except ImportError:
            pass  # No OTLP exporter — tracing still works, just no export

        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)
        logger.info("OpenTelemetry tracing configured")
    except ImportError:
        logger.info("OpenTelemetry not installed — tracing disabled")


def get_tracer():
    """Get the app tracer (or a no-op if tracing isn't configured)."""
    if _tracer:
        return _tracer
    # Return a no-op tracer
    try:
        from opentelemetry import trace
        return trace.get_tracer("light-cc")
    except ImportError:
        return _NoOpTracer()


class _NoOpSpan:
    """Minimal no-op span for when OpenTelemetry isn't available."""
    def set_attribute(self, key: str, value: Any) -> None: pass
    def set_status(self, status: Any) -> None: pass
    def record_exception(self, exc: Exception) -> None: pass
    def end(self) -> None: pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


class _NoOpTracer:
    """Minimal no-op tracer."""
    def start_span(self, name: str, **kwargs) -> _NoOpSpan:
        return _NoOpSpan()
    def start_as_current_span(self, name: str, **kwargs):
        return _NoOpSpan()


@contextmanager
def trace_span(name: str, **attributes: Any) -> Iterator[Any]:
    """Context manager for a traced span."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as span:
        for k, v in attributes.items():
            span.set_attribute(k, str(v))
        yield span


@asynccontextmanager
async def async_trace_span(name: str, **attributes: Any) -> AsyncIterator[Any]:
    """Async context manager for a traced span."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as span:
        for k, v in attributes.items():
            span.set_attribute(k, str(v))
        yield span


# ── Prometheus Metrics ───────────────────────────────────────────────

_metrics_initialized = False


def setup_metrics() -> None:
    """Initialize Prometheus metrics."""
    global _metrics_initialized
    try:
        from prometheus_client import Counter, Histogram, Gauge

        global REQUESTS_TOTAL, AGENT_LOOP_DURATION, TOOL_CALLS_TOTAL
        global TOOL_CALL_DURATION, ACTIVE_SESSIONS, TOKEN_USAGE

        REQUESTS_TOTAL = Counter(
            "lcc_requests_total",
            "Total user messages processed",
            ["user_id", "model"],
        )
        AGENT_LOOP_DURATION = Histogram(
            "lcc_agent_loop_duration_seconds",
            "Time spent in agent loop per turn",
            ["model"],
            buckets=[0.5, 1, 2, 5, 10, 30, 60, 120],
        )
        TOOL_CALLS_TOTAL = Counter(
            "lcc_tool_calls_total",
            "Total tool calls executed",
            ["tool_name"],
        )
        TOOL_CALL_DURATION = Histogram(
            "lcc_tool_call_duration_seconds",
            "Tool execution time",
            ["tool_name"],
            buckets=[0.1, 0.5, 1, 2, 5, 10, 30],
        )
        ACTIVE_SESSIONS = Gauge(
            "lcc_active_sessions",
            "Number of active WebSocket sessions",
        )
        TOKEN_USAGE = Counter(
            "lcc_token_usage_total",
            "Total tokens consumed",
            ["model", "direction"],  # direction: input or output
        )

        _metrics_initialized = True
        logger.info("Prometheus metrics configured")
    except ImportError:
        logger.info("prometheus_client not installed — metrics disabled")


def record_request(user_id: str, model: str) -> None:
    if _metrics_initialized:
        REQUESTS_TOTAL.labels(user_id=user_id, model=model).inc()


def record_tool_call(tool_name: str, duration: float) -> None:
    if _metrics_initialized:
        TOOL_CALLS_TOTAL.labels(tool_name=tool_name).inc()
        TOOL_CALL_DURATION.labels(tool_name=tool_name).observe(duration)


_audit_log = get_logger("audit")
_audit_log_is_structlog = not isinstance(_audit_log, logging.Logger)


def audit_tool_call(
    user_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    success: bool,
    duration: float,
) -> None:
    """Emit a structured audit log entry for a tool execution."""
    input_summary = {}
    for k, v in tool_input.items():
        sv = str(v)
        input_summary[k] = (sv[:200] + "...") if len(sv) > 200 else sv
    if _audit_log_is_structlog:
        _audit_log.info(
            "tool_execution",
            user_id=user_id,
            tool_name=tool_name,
            tool_input=input_summary,
            success=success,
            duration_s=round(duration, 3),
        )
    else:
        _audit_log.info(
            "tool_execution user_id=%s tool=%s success=%s duration=%.3fs",
            user_id, tool_name, success, duration,
        )


def record_tokens(model: str, input_tokens: int, output_tokens: int) -> None:
    if _metrics_initialized:
        TOKEN_USAGE.labels(model=model, direction="input").inc(input_tokens)
        TOKEN_USAGE.labels(model=model, direction="output").inc(output_tokens)


def observe_agent_loop(model: str, duration: float) -> None:
    if _metrics_initialized:
        AGENT_LOOP_DURATION.labels(model=model).observe(duration)


def session_opened() -> None:
    if _metrics_initialized:
        ACTIVE_SESSIONS.inc()


def session_closed() -> None:
    if _metrics_initialized:
        ACTIVE_SESSIONS.dec()


# ── Setup all ────────────────────────────────────────────────────────

def setup_telemetry(service_name: str = "light-cc") -> None:
    """Initialize all observability subsystems."""
    setup_structured_logging()
    setup_tracing(service_name)
    setup_metrics()
