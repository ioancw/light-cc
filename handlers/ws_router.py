"""WebSocket endpoint and event dispatch.

Handles authentication, session lifecycle, and routes incoming events
to the appropriate handler functions.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from core.auth import decode_token, get_user_by_id, is_token_revoked
from core.config import settings
from core.database import get_db
from core.hooks import fire_hooks, has_hooks
from core.rate_limit import check_rate_limit, check_rate_limit_async, check_ws_connect
from core.session import (
    _conv_sessions,
    connection_get,
    connection_set,
    conv_session_get,
    conv_session_set,
    create_connection,
    destroy_connection_async,
    destroy_conv_session,
    fork_conversation,
    get_connection_cids,
    get_or_create_conv_session,
    load_conversation,
    save_conversation,
    sync_session_to_redis,
)
from core.telemetry import session_closed, session_opened
from commands.registry import list_commands
from handlers.agent_handler import generate_title, handle_user_message, summarize_messages
from handlers.media import rebuild_render_messages
from skills.registry import list_skills

logger = logging.getLogger(__name__)


async def websocket_endpoint(
    ws: WebSocket,
    *,
    build_system_prompt,
    outputs_dir,
) -> None:
    """Main WebSocket handler -- authenticates, dispatches events, cleans up."""
    # Origin validation
    allowed_origins = settings.server.allowed_origins
    if "*" not in allowed_origins:
        origin = ws.headers.get("origin", "")
        if origin and origin not in allowed_origins:
            await ws.close(code=4003, reason="Origin not allowed")
            return

    # Rate limit connections per IP before accepting
    client_ip = ws.client.host if ws.client else "unknown"
    allowed, reason = check_ws_connect(client_ip)
    if not allowed:
        await ws.close(code=4029, reason=reason)
        return
    await ws.accept()

    # ── Authenticate ──
    # Wait for auth token in the first message
    token = None
    user_id = "default"
    user_email = ""
    user_display_name = "User"
    user_is_admin = False

    try:
        first_msg = await asyncio.wait_for(ws.receive_json(), timeout=10.0)
        if first_msg.get("type") == "auth":
            token = first_msg.get("data", {}).get("token") or first_msg.get("token")
    except (asyncio.TimeoutError, Exception):
        pass

    if token:
        payload = decode_token(token)
        if payload and payload.get("type") == "access":
            if await is_token_revoked(token):
                await ws.close(code=4001, reason="Token has been revoked")
                return
            user_id = payload["sub"]
            user_email = payload.get("email", "")
            user_is_admin = False
            db = await get_db()
            try:
                user = await get_user_by_id(db, user_id)
                if user:
                    user_display_name = user.display_name
                    user_is_admin = user.is_admin
            finally:
                await db.close()
        else:
            await ws.close(code=4001, reason="Invalid or expired token")
            return

    session_id = str(uuid.uuid4())
    create_connection(session_id, user_id=user_id)
    connection_set(session_id, "is_admin", user_is_admin)
    session_opened()

    if has_hooks("SessionStart"):
        await fire_hooks("SessionStart", {"session_id": session_id, "user_id": user_id})

    # Per-connection state
    pending_permissions: dict[str, dict[str, asyncio.Future]] = {}
    agent_tasks: dict[str, asyncio.Task] = {}
    MAX_CONCURRENT_AGENTS = 3

    # ── Send helpers ──

    async def send_event(event_type: str, data: dict[str, Any], cid: str | None = None) -> None:
        try:
            msg: dict[str, Any] = {"type": event_type, "data": data}
            if cid:
                msg["cid"] = cid
            await ws.send_json(msg)
        except Exception:
            pass

    def make_send_event(cid: str):
        async def scoped_send(event_type: str, data: dict[str, Any]) -> None:
            await send_event(event_type, data, cid=cid)
        return scoped_send

    # ── Register notifications ──
    from core.scheduler import register_user_sender, unregister_user_sender
    register_user_sender(user_id, send_event)

    from tools.subagent import set_notification_callback
    from tools.tasks import set_task_notify_callback

    async def bg_notify(task_id: str, message: str) -> None:
        await send_event("notification", {"task_id": task_id, "message": message})

    async def task_notify(event_type: str, data: dict[str, Any]) -> None:
        await send_event(event_type, data)

    set_notification_callback(bg_notify, session_id=session_id)
    set_task_notify_callback(task_notify, session_id=session_id)

    # ── Send connected event ──
    skills_for_client = [
        {"name": s.name, "description": s.description, "argument_hint": s.argument_hint}
        for s in list_skills() if s.user_invocable
    ] + [
        {"name": c.name, "description": c.description, "argument_hint": c.argument_hint}
        for c in list_commands()
    ] + [
        {"name": "context", "description": "Show context window usage breakdown", "argument_hint": ""},
        {"name": "plugin", "description": "Install, list, update, or uninstall plugins", "argument_hint": "install|list|update|uninstall <name-or-url>"},
        {"name": "schedule", "description": "Create, list, enable, disable, or delete scheduled agent tasks", "argument_hint": "create|list|enable|disable|delete|runs|run"},
        {"name": "reload", "description": "Reload all skills, commands, and project config from disk", "argument_hint": ""},
    ]
    await send_event("connected", {
        "session_id": session_id,
        "model": settings.model,
        "available_models": settings.available_models,
        "skills": skills_for_client,
        "suggestions": settings.suggestions,
        "user": {
            "id": user_id,
            "email": user_email,
            "display_name": user_display_name,
        },
    })

    # ── Event handlers ──

    async def _handle_user_msg(cid: str, data: dict[str, Any]) -> None:
        if not cid:
            await send_event("error", {"message": "Missing cid"})
            return

        allowed, reason = await check_rate_limit_async(user_id, "message")
        if not allowed:
            await send_event("error", {"message": reason}, cid=cid)
            return

        active_count = sum(1 for t in agent_tasks.values() if not t.done())
        if active_count >= MAX_CONCURRENT_AGENTS:
            await send_event("error", {"message": "Too many active conversations. Please wait for one to finish."}, cid=cid)
            return
        if cid in agent_tasks and not agent_tasks[cid].done():
            await send_event("error", {"message": "This conversation is already generating."}, cid=cid)
            return

        cs = get_or_create_conv_session(cid, session_id)

        # If this session has no conversation_id but the cid references a server
        # conversation (e.g. after WS reconnect), recover from DB to avoid
        # creating a duplicate conversation.
        if cs.get("conversation_id") is None and not cs.get("messages"):
            # The frontend sends cids like "srv_<server_id>" for server-loaded convs
            server_id = cid[4:] if cid.startswith("srv_") else None
            # Also check if data contains a conversation_id hint
            if not server_id:
                server_id = data.get("conversation_id")
            if server_id:
                try:
                    messages = await load_conversation(server_id)
                    if messages is not None:
                        cs["messages"] = messages
                        cs["conversation_id"] = server_id
                except Exception as e:
                    logger.debug(f"Failed to recover conversation {server_id}: {e}")
        scoped_send = make_send_event(cid)
        cid_perms = pending_permissions.setdefault(cid, {})
        task = asyncio.create_task(
            handle_user_message(
                session_id, cid, data, scoped_send, cid_perms,
                build_system_prompt=build_system_prompt,
                outputs_dir=outputs_dir,
            )
        )
        task.add_done_callback(lambda t, c=cid: agent_tasks.pop(c, None))
        agent_tasks[cid] = task

    async def _handle_permission_response(cid: str | None, data: dict[str, Any]) -> None:
        req_id = data.get("request_id", "")
        cid_perms = pending_permissions.get(cid, {}) if cid else {}
        future = cid_perms.get(req_id)
        if not future:
            for perms in pending_permissions.values():
                future = perms.get(req_id)
                if future:
                    break
        if future and not future.done():
            future.set_result(data.get("allowed", False))

    async def _handle_cancel(cid: str | None) -> None:
        if cid:
            task = agent_tasks.get(cid)
            if task and not task.done():
                task.cancel()
        else:
            for task in agent_tasks.values():
                if not task.done():
                    task.cancel()

    async def _handle_clear(cid: str | None) -> None:
        if cid and cid in _conv_sessions:
            await save_conversation(cid)
            destroy_conv_session(cid)

    async def _handle_resume(cid: str | None, data: dict[str, Any]) -> None:
        conv_id = data.get("conversation_id", "")
        if not (conv_id and cid):
            return
        try:
            messages = await load_conversation(conv_id)
            cs = get_or_create_conv_session(cid, session_id)
            cs["messages"] = messages
            cs["conversation_id"] = conv_id
            from core.db_models import Conversation as ConvModel
            from sqlalchemy import select as sql_select
            db = await get_db()
            try:
                result = await db.execute(
                    sql_select(ConvModel.model).where(ConvModel.id == conv_id)
                )
                conv_model = result.scalar_one_or_none()
                if conv_model:
                    cs["active_model"] = conv_model
            finally:
                await db.close()
            render_messages = rebuild_render_messages(messages)

            ctx_tokens = 0
            try:
                from core.context import count_message_tokens
                ctx_tokens = await count_message_tokens(messages, "", None)
            except Exception:
                pass

            await send_event("conversation_loaded", {
                "conversation_id": conv_id,
                "message_count": len(messages),
                "model": conv_model or settings.model,
                "messages": render_messages,
                "context_tokens": ctx_tokens,
            }, cid=cid)
        except Exception as e:
            logger.error(f"Failed to load conversation {conv_id}: {e}", exc_info=True)
            await send_event("error", {"message": f"Failed to load conversation: {e}"}, cid=cid)

    async def _handle_revert_checkpoint(cid: str | None, data: dict[str, Any]) -> None:
        cp_key = cid or session_id
        from core.checkpoints import revert_last, revert_to_turn, list_checkpoints
        turn = data.get("turn")
        if turn is not None:
            reverted = revert_to_turn(cp_key, int(turn))
        else:
            reverted = revert_last(cp_key)
        await send_event("checkpoint_reverted", {
            "reverted_files": reverted,
            "remaining": len(list_checkpoints(cp_key)),
        }, cid=cid)

    async def _handle_list_checkpoints(cid: str | None) -> None:
        cp_key = cid or session_id
        from core.checkpoints import list_checkpoints
        cps = list_checkpoints(cp_key)
        await send_event("checkpoints", {
            "entries": [
                {"file_path": cp.file_path, "turn": cp.turn, "size": cp.size, "existed": cp.existed}
                for cp in cps
            ],
        }, cid=cid)

    async def _handle_fork(cid: str | None, data: dict[str, Any]) -> None:
        fork_conv_id = data.get("conversation_id", "")
        if fork_conv_id:
            try:
                new_conv_id, messages = await fork_conversation(fork_conv_id, user_id)
                fork_cid = f"fork_{new_conv_id}"
                cs = get_or_create_conv_session(fork_cid, session_id)
                cs["messages"] = messages
                cs["conversation_id"] = new_conv_id
                await send_event("conversation_forked", {
                    "source_conversation_id": fork_conv_id,
                    "conversation_id": new_conv_id,
                    "message_count": len(messages),
                }, cid=cid)
            except ValueError as e:
                await send_event("error", {"message": str(e)}, cid=cid)

    async def _handle_set_system_prompt(data: dict[str, Any]) -> None:
        connection_set(session_id, "user_system_prompt", data.get("text", ""))

    async def _handle_set_permission_mode(data: dict[str, Any]) -> None:
        from core.permission_modes import PermissionMode
        mode_str = data.get("mode", "")
        try:
            mode = PermissionMode(mode_str)
            connection_set(session_id, "permission_mode", mode.value)
            await send_event("permission_mode_changed", {"mode": mode.value})
        except ValueError:
            await send_event("error", {"message": f"Unknown permission mode: {mode_str}"})

    async def _handle_cycle_permission_mode() -> None:
        from core.permission_modes import PermissionMode
        current = PermissionMode(connection_get(session_id, "permission_mode") or "default")
        new_mode = current.next()
        connection_set(session_id, "permission_mode", new_mode.value)
        await send_event("permission_mode_changed", {"mode": new_mode.value})

    async def _handle_generate_title(cid: str | None) -> None:
        if cid:
            conv_id = conv_session_get(cid, "conversation_id")
            msgs = conv_session_get(cid, "messages") or []
        else:
            conv_id = None
            msgs = []
        if msgs and len(msgs) >= 2:
            title = await generate_title(msgs[:4])
            if title and conv_id:
                from core.db_models import Conversation
                from sqlalchemy import update as sql_update
                db = await get_db()
                try:
                    await db.execute(
                        sql_update(Conversation).where(Conversation.id == conv_id).values(title=title)
                    )
                    await db.commit()
                finally:
                    await db.close()
            await send_event("title_updated", {"conversation_id": conv_id or "", "title": title or ""}, cid=cid)

    async def _handle_summarize_context(cid: str | None) -> None:
        if cid:
            msgs = conv_session_get(cid, "messages") or []
        else:
            msgs = []
        if len(msgs) < 6:
            await send_event("error", {"message": "Not enough messages to summarize"}, cid=cid)
        else:
            try:
                summary = await summarize_messages(msgs)
                recent = msgs[-4:]
                new_msgs = [{"role": "user", "content": f"[Previous conversation summary: {summary}]"}] + recent
                if cid:
                    conv_session_set(cid, "messages", new_msgs)
                await send_event("context_summarized", {
                    "original_count": len(msgs),
                    "new_count": len(new_msgs),
                    "summary": summary,
                }, cid=cid)
            except Exception as e:
                logger.error(f"Context summarization failed: {e}", exc_info=True)
                await send_event("error", {"message": f"Summarization failed: {e}"}, cid=cid)

    async def _handle_set_model(cid: str | None, data: dict[str, Any]) -> None:
        model_id = data.get("model", "")
        if model_id in settings.available_models:
            if cid:
                conv_session_set(cid, "active_model", model_id)
            await send_event("model_changed", {"model": model_id}, cid=cid)
            await sync_session_to_redis(session_id)
        else:
            await send_event("error", {"message": f"Unknown model: {model_id}"}, cid=cid)

    # ── Main event loop ──

    try:
        while True:
            raw = await ws.receive_json()
            event_type = raw.get("type", "")
            data = raw.get("data", {})
            cid = raw.get("cid")

            if event_type == "user_message":
                await _handle_user_msg(cid, data)
            elif event_type == "permission_response":
                await _handle_permission_response(cid, data)
            elif event_type == "cancel_generation":
                await _handle_cancel(cid)
            elif event_type == "clear_conversation":
                await _handle_clear(cid)
            elif event_type == "resume_conversation":
                await _handle_resume(cid, data)
            elif event_type == "revert_checkpoint":
                await _handle_revert_checkpoint(cid, data)
            elif event_type == "list_checkpoints":
                await _handle_list_checkpoints(cid)
            elif event_type == "fork_conversation":
                await _handle_fork(cid, data)
            elif event_type == "set_system_prompt":
                await _handle_set_system_prompt(data)
            elif event_type == "set_permission_mode":
                await _handle_set_permission_mode(data)
            elif event_type == "cycle_permission_mode":
                await _handle_cycle_permission_mode()
            elif event_type == "generate_title":
                await _handle_generate_title(cid)
            elif event_type == "summarize_context":
                await _handle_summarize_context(cid)
            elif event_type == "retry":
                # Retry: remove last assistant message and re-send the last user message
                if cid:
                    msgs = conv_session_get(cid, "messages") or []
                    # Pop trailing assistant message(s) to get back to the last user turn
                    while msgs and msgs[-1].get("role") != "user":
                        msgs.pop()
                    if msgs:
                        last_user = msgs[-1]
                        text = last_user.get("content", "")
                        if isinstance(text, list):
                            text = " ".join(
                                b.get("text", "") for b in text
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        conv_session_set(cid, "messages", msgs)
                        await _handle_user_msg(cid, {"text": text})
                    else:
                        await send_event("error", {"message": "No message to retry"}, cid=cid)
            elif event_type == "set_model":
                await _handle_set_model(cid, data)
            elif event_type == "rollback_compression":
                if cid:
                    from core.context import rollback_compression
                    restored = rollback_compression(cid)
                    if restored:
                        conv_session_set(cid, "messages", restored)
                        await send_event("compression_rolled_back", {
                            "message_count": len(restored),
                        }, cid=cid)
                    else:
                        await send_event("error", {"message": "No compression snapshot available"}, cid=cid)
            else:
                logger.debug(f"Unknown event type: {event_type}")

    except WebSocketDisconnect:
        logger.info(f"Session {session_id} disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Save all active conversation sub-sessions
        for active_cid in list(get_connection_cids(session_id)):
            try:
                await save_conversation(active_cid)
            except Exception:
                logger.error(f"Failed to save conversation {active_cid} on disconnect", exc_info=True)
        # Cancel all running agent tasks
        for task in agent_tasks.values():
            if not task.done():
                task.cancel()
        if has_hooks("SessionEnd"):
            try:
                await fire_hooks("SessionEnd", {"session_id": session_id, "user_id": user_id})
            except Exception:
                pass
        unregister_user_sender(user_id, send_event)
        from tools.subagent import remove_notification_callback
        from tools.tasks import remove_task_notify_callback
        remove_notification_callback(session_id)
        remove_task_notify_callback(session_id)
        from core.checkpoints import clear_checkpoints
        for active_cid in list(get_connection_cids(session_id)):
            clear_checkpoints(active_cid)
        clear_checkpoints(session_id)
        await destroy_connection_async(session_id)
        session_closed()
