"""Global registry for in-flight agent tasks and per-cid event subscribers.

Tasks and subscribers are keyed by conversation id (``cid``) rather than
WebSocket session id so that an agent turn can survive a transient WS
disconnect. When the client reconnects it can either (a) resubscribe to
continue streaming a still-running turn, or (b) read the persisted final
state from the database if the turn ended while the socket was gone.

There are three pieces of shared state:

- ``_agent_tasks`` -- one active ``asyncio.Task`` per cid.
- ``_subscribers`` -- the set of WS send callbacks currently receiving live
  events for a cid. Empty when no client is listening.
- ``_pending_permissions`` -- per-cid permission-request futures. Lives here
  (not on the WS connection) so a reconnecting tab can still resolve a
  prompt the original tab opened.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Subscriber signature: (event_type, data, cid) -> Awaitable[None]
RawSend = Callable[[str, dict[str, Any], str], Awaitable[None]]

_agent_tasks: dict[str, asyncio.Task] = {}
_subscribers: dict[str, set[RawSend]] = {}
_pending_permissions: dict[str, dict[str, asyncio.Future]] = {}


# ── Tasks ─────────────────────────────────────────────────────────────

def register_task(cid: str, task: asyncio.Task) -> None:
    _agent_tasks[cid] = task
    task.add_done_callback(lambda t, c=cid: _agent_tasks.pop(c, None))


def get_task(cid: str) -> asyncio.Task | None:
    return _agent_tasks.get(cid)


def is_generating(cid: str) -> bool:
    t = _agent_tasks.get(cid)
    return bool(t and not t.done())


def generating_cids() -> set[str]:
    return {c for c, t in _agent_tasks.items() if not t.done()}


def cancel_task(cid: str) -> bool:
    t = _agent_tasks.get(cid)
    if t and not t.done():
        t.cancel()
        return True
    return False


# ── Subscribers ───────────────────────────────────────────────────────

def subscribe(cid: str, send_fn: RawSend) -> None:
    _subscribers.setdefault(cid, set()).add(send_fn)


def unsubscribe(cid: str, send_fn: RawSend) -> None:
    subs = _subscribers.get(cid)
    if subs:
        subs.discard(send_fn)
        if not subs:
            del _subscribers[cid]


def unsubscribe_all(send_fn: RawSend) -> None:
    """Remove ``send_fn`` from every subscription set (call on WS disconnect)."""
    for cid in list(_subscribers.keys()):
        subs = _subscribers.get(cid)
        if subs:
            subs.discard(send_fn)
            if not subs:
                del _subscribers[cid]


async def broadcast(cid: str, event_type: str, data: dict[str, Any]) -> None:
    subs = _subscribers.get(cid)
    if not subs:
        return
    for send_fn in list(subs):
        try:
            await send_fn(event_type, data, cid)
        except Exception:
            # A dead subscriber shouldn't break delivery to the rest.
            logger.debug("broadcast send failed for cid=%s", cid, exc_info=True)


# ── Permission futures ───────────────────────────────────────────────

def add_pending_permission(cid: str, request_id: str, future: asyncio.Future) -> None:
    _pending_permissions.setdefault(cid, {})[request_id] = future


def pop_pending_permission(cid: str, request_id: str) -> asyncio.Future | None:
    return _pending_permissions.get(cid, {}).pop(request_id, None)


def find_pending_permission(request_id: str) -> asyncio.Future | None:
    """Fallback lookup when the cid isn't known (e.g. response missing envelope)."""
    for perms in _pending_permissions.values():
        fut = perms.get(request_id)
        if fut is not None:
            return fut
    return None


def clear_permissions(cid: str) -> None:
    _pending_permissions.pop(cid, None)
