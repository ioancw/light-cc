"""Web tools — fetch URLs and search the web."""

from __future__ import annotations

import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from tools.registry import register_tool

_HTTP_TIMEOUT = 30

# Hostnames that should always be blocked (cloud metadata endpoints, etc.)
_BLOCKED_HOSTS = {
    "metadata.google.internal",
    "metadata.goog",
    "169.254.169.254",  # AWS/GCP/Azure metadata
}


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Check if a URL is safe to fetch (not an internal/private address).

    Returns (is_safe, reason).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL"

    # Only allow HTTP(S)
    if parsed.scheme not in ("http", "https"):
        return False, f"Scheme '{parsed.scheme}' not allowed — only http/https"

    hostname = parsed.hostname
    if not hostname:
        return False, "No hostname in URL"

    # Check blocked hostnames
    if hostname.lower() in _BLOCKED_HOSTS:
        return False, f"Access to '{hostname}' is blocked"

    # Resolve hostname and check for private IPs
    try:
        addrinfo = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
        for _, _, _, _, sockaddr in addrinfo:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False, f"Access to private/internal address {ip} is blocked"
    except socket.gaierror:
        return False, f"Could not resolve hostname '{hostname}'"

    return True, ""


async def handle_web_fetch(tool_input: dict[str, Any]) -> str:
    """Fetch content from a URL."""
    url = tool_input.get("url", "")
    if not url:
        return json.dumps({"error": "No URL provided"})

    # SSRF protection — block private/internal addresses
    safe, reason = _is_safe_url(url)
    if not safe:
        return json.dumps({"error": f"URL blocked: {reason}"})

    method = tool_input.get("method", "GET").upper()
    headers = tool_input.get("headers", {})
    body = tool_input.get("body")

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            if method == "POST":
                resp = await client.post(url, headers=headers, json=body if body else None)
            else:
                resp = await client.get(url, headers=headers)

            content_type = resp.headers.get("content-type", "")

            if "json" in content_type:
                try:
                    data = resp.json()
                    body_text = json.dumps(data, indent=2)
                except Exception:
                    body_text = resp.text
            else:
                body_text = resp.text

            return json.dumps({
                "status_code": resp.status_code,
                "content_type": content_type,
                "body": body_text[:50000],
            })
    except httpx.TimeoutException:
        return json.dumps({"error": f"Request timed out after {_HTTP_TIMEOUT}s"})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def handle_web_search(tool_input: dict[str, Any]) -> str:
    """Search the web via DuckDuckGo."""
    query = tool_input.get("query", "")
    if not query:
        return json.dumps({"error": "No query provided"})

    max_results = tool_input.get("max_results", 5)

    try:
        try:
            from ddgs import DDGS  # renamed from duckduckgo-search
        except ImportError:
            from duckduckgo_search import DDGS  # legacy fallback

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        formatted = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in results
        ]
        return json.dumps({"results": formatted, "count": len(formatted)})
    except ImportError:
        return json.dumps({"error": "ddgs package not installed. Run: pip install ddgs"})
    except Exception as e:
        return json.dumps({"error": str(e)})


register_tool(
    name="WebFetch",
    aliases=["web_fetch"],
    description=(
        "Fetch content from a public HTTP/HTTPS URL. Returns status code and body text. "
        "IMPORTANT: Only for external URLs on the public internet. "
        "Do NOT use for local files (use Read), localhost/127.0.0.1 (use Bash with curl), "
        "or file:// URLs (use Read). Private/internal addresses are blocked (SSRF protection). "
        "For web pages, returns extracted text. For APIs, returns raw JSON. "
        "Supports GET and POST methods. Timeout is 30 seconds."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full URL to fetch (must be https:// or http://). Not for localhost or file:// paths.",
            },
            "method": {
                "type": "string",
                "enum": ["GET", "POST"],
                "description": "HTTP method (default: GET)",
            },
            "headers": {
                "type": "object",
                "description": "Optional HTTP headers as key-value pairs",
            },
            "body": {
                "type": "object",
                "description": "Optional JSON body for POST requests",
            },
        },
        "required": ["url"],
    },
    handler=handle_web_fetch,
)

register_tool(
    name="WebSearch",
    aliases=["web_search"],
    description=(
        "Search the web using DuckDuckGo. Returns titles, URLs, and text snippets. "
        "Use this to find current information, documentation, or answers to factual questions. "
        "Follow up with WebFetch to read full pages from the results. "
        "Default returns 5 results."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (e.g. 'python pandas groupby tutorial')",
            },
            "max_results": {
                "type": "integer",
                "description": "Max results to return (default 5). Increase for broader searches.",
            },
        },
        "required": ["query"],
    },
    handler=handle_web_search,
)
