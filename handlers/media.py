"""Media detection and rendering — images, charts, tables, HTML embeds.

Extracts file paths from tool stdout and sends rich content to the frontend.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# ── Constants ──

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}

MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}

SendEvent = Callable[[str, dict[str, Any]], Awaitable[None]]


# ── Extraction helpers (sync, for conversation reload) ──

def extract_images_from_result(result) -> list[dict[str, str]]:
    """Extract base64-encoded images from file paths found in tool result stdout."""
    images = []
    try:
        if isinstance(result, list):
            text_parts = [b.get("text", "") for b in result if isinstance(b, dict) and b.get("type") == "text"]
            result = "\n".join(text_parts)
        parsed = json.loads(result) if isinstance(result, str) else result
        stdout = parsed.get("stdout", "") if isinstance(parsed, dict) else (result if isinstance(result, str) else "")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return images

    if not stdout:
        return images

    for line in stdout.strip().splitlines():
        line = line.strip()
        try:
            p = Path(line)
            if not p.exists():
                continue
            ext = p.suffix.lower()
            mime = MIME_MAP.get(ext)
            if mime and p.stat().st_size < 10 * 1024 * 1024:
                data = base64.b64encode(p.read_bytes()).decode("ascii")
                images.append({"mime": mime, "data": data, "name": p.name})
        except (OSError, ValueError):
            continue
    return images


def extract_chart_from_result(result) -> dict[str, str] | None:
    """Extract Plotly chart JSON from file paths found in tool result stdout."""
    try:
        if isinstance(result, list):
            text_parts = [b.get("text", "") for b in result if isinstance(b, dict) and b.get("type") == "text"]
            result = "\n".join(text_parts)
        parsed = json.loads(result) if isinstance(result, str) else result
        stdout = parsed.get("stdout", "") if isinstance(parsed, dict) else (result if isinstance(result, str) else "")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None

    if not stdout:
        return None

    for line in stdout.strip().splitlines():
        line = line.strip()
        try:
            p = Path(line)
            if p.exists() and p.name.endswith(".plotly.json"):
                chart_json = p.read_text(encoding="utf-8")
                json.loads(chart_json)  # validate
                return {"title": p.stem.replace(".plotly", ""), "plotlyJson": chart_json}
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    return None


# ── Message rebuilding (for conversation reload) ──

def rebuild_render_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild frontend-renderable messages from stored API messages.

    Extracts text, tool_use, and tool_result blocks so the frontend can
    reconstruct tool call panels with images/charts.
    """
    # First pass: collect tool_result by tool_use_id
    tool_results: dict[str, dict[str, Any]] = {}
    for msg in messages:
        content = msg.get("content", "")
        if msg.get("role") == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id")
                    if tid:
                        tool_results[tid] = block

    # Second pass: build render messages
    render_messages: list[dict[str, Any]] = []
    for msg in messages:
        try:
            content = msg.get("content", "")

            if msg["role"] == "user":
                if isinstance(content, str):
                    render_messages.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    text_parts = [b.get("text", "") for b in content
                                  if isinstance(b, dict) and b.get("type") == "text"]
                    if text_parts:
                        render_messages.append({"role": "user", "content": "\n".join(text_parts)})

            elif msg["role"] == "assistant":
                if isinstance(content, str):
                    render_messages.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    text_parts = []
                    tool_calls = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            tc_id = block.get("id", "")
                            tc: dict[str, Any] = {
                                "id": tc_id,
                                "name": block.get("name", "tool"),
                                "input": block.get("input", {}),
                                "status": "done",
                            }
                            tr = tool_results.get(tc_id)
                            if tr:
                                result_content = tr.get("content", "")
                                tc["result"] = result_content
                                tc["is_error"] = tr.get("is_error", False)
                                if tc["is_error"]:
                                    tc["status"] = "error"
                                try:
                                    tc["images"] = extract_images_from_result(result_content)
                                except Exception:
                                    tc["images"] = []
                                try:
                                    chart = extract_chart_from_result(result_content)
                                    if chart:
                                        tc["chart"] = chart
                                except Exception:
                                    pass
                            tool_calls.append(tc)

                    rm: dict[str, Any] = {
                        "role": "assistant",
                        "content": "\n".join(text_parts) if text_parts else "",
                    }
                    if tool_calls:
                        rm["toolCalls"] = tool_calls
                    render_messages.append(rm)
        except Exception as e:
            logger.warning(f"Skipping malformed message during rebuild: {e}")
            try:
                content = msg.get("content", "")
                if isinstance(content, str) and content:
                    render_messages.append({"role": msg.get("role", "assistant"), "content": content})
            except Exception:
                pass

    return render_messages


# ── Streaming send helpers (async, for live tool execution) ──

async def send_images_if_any(
    tool_id: str,
    result: str,
    send_event: SendEvent,
) -> None:
    """Detect artifacts or file paths in tool result -- send rich content to frontend.

    Checks for structured artifacts first, then falls back to stdout path scanning.
    """
    try:
        parsed = json.loads(result)

        # Prefer structured artifacts if present
        artifacts = parsed.get("artifacts", [])
        if artifacts:
            for a in artifacts:
                if not isinstance(a, dict):
                    continue
                a_type = a.get("type", "")
                a_path = a.get("path", "")
                p = Path(a_path) if a_path else None

                if a_type == "chart" and p and p.exists():
                    try:
                        chart_json = p.read_text(encoding="utf-8")
                        json.loads(chart_json)
                        await send_event("chart", {
                            "tool_id": tool_id,
                            "title": a.get("title", p.stem.replace(".plotly", "")),
                            "plotly_json": chart_json,
                        })
                    except (json.JSONDecodeError, OSError):
                        pass
                elif a_type == "html" and p and p.exists():
                    try:
                        html_content = p.read_text(encoding="utf-8")
                        await send_event("html_embed", {
                            "tool_id": tool_id,
                            "name": a.get("name", p.stem),
                            "html": html_content,
                        })
                    except OSError:
                        pass
                elif a_type == "image" and p and p.exists():
                    mime = a.get("mime") or MIME_MAP.get(p.suffix.lower(), "image/png")
                    data = base64.b64encode(p.read_bytes()).decode()
                    await send_event("image", {
                        "tool_id": tool_id,
                        "name": p.stem,
                        "mime_type": mime,
                        "data_base64": data,
                    })
            return  # Artifacts handled, skip stdout scanning

        # Fallback: scan stdout for file paths
        stdout = parsed.get("stdout", "")
        for line in stdout.strip().splitlines():
            line = line.strip()
            p = Path(line)
            if not p.exists():
                continue

            # Interactive Plotly chart (*.plotly.json)
            if p.name.endswith(".plotly.json"):
                try:
                    chart_json = p.read_text(encoding="utf-8")
                    json.loads(chart_json)  # validate
                    await send_event("chart", {
                        "tool_id": tool_id,
                        "title": p.stem.replace(".plotly", ""),
                        "plotly_json": chart_json,
                    })
                except (json.JSONDecodeError, OSError):
                    pass
                continue

            # HTML embeds (D3, custom visualizations)
            if p.suffix.lower() == ".html":
                try:
                    html_content = p.read_text(encoding="utf-8")
                    await send_event("html_embed", {
                        "tool_id": tool_id,
                        "name": p.stem,
                        "html": html_content,
                    })
                except OSError:
                    pass
                continue

            # Static images
            if p.suffix.lower() in IMAGE_EXTS:
                data = base64.b64encode(p.read_bytes()).decode()
                mime = MIME_MAP.get(p.suffix.lower(), "image/png")
                await send_event("image", {
                    "tool_id": tool_id,
                    "name": p.stem,
                    "mime_type": mime,
                    "data_base64": data,
                })
    except (json.JSONDecodeError, ValueError):
        pass


async def send_chart_if_any(
    tool_id: str,
    tool_name: str,
    result: str,
    send_event: SendEvent,
) -> None:
    """If the tool was CreateChart, send the Plotly figure JSON."""
    from tools.registry import resolve_tool_name
    if resolve_tool_name(tool_name) != "CreateChart":
        return
    try:
        parsed = json.loads(result)
        if parsed.get("inline"):
            from tools.chart import get_last_figure

            fig = get_last_figure()
            if fig is not None:
                await send_event("chart", {
                    "tool_id": tool_id,
                    "title": parsed.get("title", "Chart"),
                    "plotly_json": fig.to_json(),
                })
    except (json.JSONDecodeError, ImportError):
        pass


async def send_tables_if_any(
    tool_id: str,
    tool_name: str,
    result: str,
    send_event: SendEvent,
) -> None:
    """Detect HTML tables in tool results and CSV files in stdout."""
    try:
        parsed = json.loads(result)

        # 1. Data tools return table HTML directly
        for key in ("head_html", "describe_html", "table_html"):
            html = parsed.get(key)
            if html:
                logger.info(f"[tables] Sending {key} table for tool {tool_id} ({len(html)} chars)")
                await send_event("table", {
                    "tool_id": tool_id,
                    "html": html,
                })

        # 2. Detect CSV files in stdout from any tool (python_exec, bash)
        stdout = parsed.get("stdout", "")
        if stdout:
            for line in stdout.strip().splitlines():
                line = line.strip()
                p = Path(line)
                if p.suffix.lower() == ".csv" and p.exists():
                    try:
                        import pandas as pd
                        from tools.data_tools import _df_to_html

                        df = pd.read_csv(p)
                        preview_html = _df_to_html(df, title=p.stem)
                        await send_event("table", {
                            "tool_id": tool_id,
                            "html": preview_html,
                        })
                        desc = df.describe(include="all")
                        desc_html = _df_to_html(desc, max_rows=50, title="Summary Statistics")
                        await send_event("table", {
                            "tool_id": tool_id,
                            "html": desc_html,
                        })
                    except Exception:
                        pass

    except (json.JSONDecodeError, ValueError):
        pass
