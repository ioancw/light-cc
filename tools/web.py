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


_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_TAVILY_TOPICS = {"general", "news", "finance"}
_TAVILY_DEPTHS = {"basic", "advanced"}


async def _tavily_search(query: str, tool_input: dict[str, Any], api_key: str) -> str:
    """Search the web via Tavily. Optimized for LLM consumption."""
    depth = tool_input.get("search_depth", "basic")
    if depth not in _TAVILY_DEPTHS:
        depth = "basic"

    topic = tool_input.get("topic", "general")
    if topic not in _TAVILY_TOPICS:
        topic = "general"

    max_results = int(tool_input.get("max_results", 5))
    include_answer = bool(tool_input.get("include_answer", True))

    payload: dict[str, Any] = {
        "query": query,
        "search_depth": depth,
        "topic": topic,
        "max_results": max(1, min(max_results, 20)),
        "include_answer": include_answer,
    }

    include_domains = tool_input.get("include_domains")
    if include_domains:
        payload["include_domains"] = include_domains
    exclude_domains = tool_input.get("exclude_domains")
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                _TAVILY_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.TimeoutException:
        return json.dumps({
            "error": f"Tavily request timed out after {_HTTP_TIMEOUT}s",
            "hint": "Try search_depth='basic' or a narrower query.",
        })
    except Exception as e:
        return json.dumps({"error": f"Tavily request failed: {e}"})

    if resp.status_code == 401:
        return json.dumps({
            "error": "Tavily API key is invalid or missing.",
            "hint": "Set TAVILY_API_KEY in .env (get one at https://app.tavily.com).",
        })
    if resp.status_code == 429:
        return json.dumps({
            "error": "Tavily rate limit exceeded.",
            "hint": "Wait a few seconds or upgrade your Tavily plan.",
        })
    if resp.status_code >= 400:
        return json.dumps({
            "error": f"Tavily returned HTTP {resp.status_code}",
            "body": resp.text[:500],
        })

    try:
        data = resp.json()
    except Exception:
        return json.dumps({"error": "Tavily returned non-JSON response", "body": resp.text[:500]})

    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", "")[:500],
            "score": r.get("score"),
        }
        for r in data.get("results", [])
    ]
    out: dict[str, Any] = {
        "provider": "tavily",
        "results": results,
        "count": len(results),
    }
    if data.get("answer"):
        out["answer"] = data["answer"]
    return json.dumps(out)


async def _ddg_search(query: str, max_results: int) -> str:
    """Fallback web search via DuckDuckGo when no Tavily key is configured."""
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
        return json.dumps({
            "provider": "duckduckgo",
            "results": formatted,
            "count": len(formatted),
        })
    except ImportError:
        return json.dumps({
            "error": "No search backend available.",
            "hint": "Set TAVILY_API_KEY in .env, or run: pip install ddgs",
        })
    except Exception as e:
        return json.dumps({"error": f"DuckDuckGo search failed: {e}"})


async def handle_web_search(tool_input: dict[str, Any]) -> str:
    """Search the web. Uses Tavily when TAVILY_API_KEY is set, falls back to DuckDuckGo."""
    query = tool_input.get("query", "")
    if not query:
        return json.dumps({"error": "No query provided"})

    from core.config import settings
    api_key = settings.tavily_api_key
    if api_key:
        return await _tavily_search(query, tool_input, api_key)

    max_results = int(tool_input.get("max_results", 5))
    return await _ddg_search(query, max_results)


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
        "Search the web and return LLM-ready results. "
        "Primary backend: Tavily (set TAVILY_API_KEY in .env). Fallback: DuckDuckGo. "
        "Returns a list of results with title, url, snippet, and (Tavily only) a relevance score. "
        "When include_answer=true (default), Tavily also returns a direct synthesized answer — "
        "prefer reading that first, then use the results for citations. "
        "Use search_depth='advanced' for research-grade queries (slower, richer). "
        "Follow up with WebFetch to read full pages when snippets aren't enough. "
        "Use topic='news' for current events and topic='finance' for markets/tickers."
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
                "description": "Max results to return (default 5, max 20).",
            },
            "search_depth": {
                "type": "string",
                "enum": ["basic", "advanced"],
                "description": "Tavily only. 'basic' is fast; 'advanced' does deeper crawling. Default 'basic'.",
            },
            "topic": {
                "type": "string",
                "enum": ["general", "news", "finance"],
                "description": "Tavily only. Narrow the index. Default 'general'.",
            },
            "include_answer": {
                "type": "boolean",
                "description": "Tavily only. Include a synthesized answer string (default true).",
            },
            "include_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tavily only. Whitelist of domains (e.g. ['arxiv.org', 'nature.com']).",
            },
            "exclude_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tavily only. Blacklist of domains.",
            },
        },
        "required": ["query"],
    },
    handler=handle_web_search,
)
