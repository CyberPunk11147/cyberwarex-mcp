"""x402 SEO Audit -- MCP server (stdio).

Exposes the SEO Audit service (seo.cyberwarex.com) as MCP tools: a full on-page audit, a keyword
trends lookup, and a free meta-tag peek -- so any MCP-speaking agent gets actionable SEO fields
without scraping HTML or managing API keys.

    seo_audit(url) -> full on-page audit JSON (meta, headings, images, schema.org, canonical, ...)
    seo_trends(keyword, geo, timeframe) -> keyword search-interest trend
    seo_meta(url) -> free meta-tag peek (title, description, canonical) -- no payment required

It is a thin, stateless proxy to the HTTP service (default https://seo.cyberwarex.com, override with
SEO_BASE_URL -- set this to the public funnel URL for remote agents).

Payment model: /audit and /trends are x402-gated; /meta is free. This MCP server forwards an
X-PAYMENT header when the caller provides one via the SEO_X_PAYMENT env var, and otherwise surfaces
the 402 payment requirement (the full `accepts` + `extensions.bazaar` block) back to the agent so
*its* x402 client can pay and retry. Payment stays on the calling agent's wallet -- this server never
holds keys.

Run:  python mcp_server.py       (speaks MCP over stdio)
"""
from __future__ import annotations

import asyncio
import json
import os

import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

BASE_URL = (os.environ.get("SEO_BASE_URL") or "https://seo.cyberwarex.com").rstrip("/")
X_PAYMENT = os.environ.get("SEO_X_PAYMENT", "").strip()
RAPIDAPI_SECRET = os.environ.get("SEO_RAPIDAPI_SECRET", "").strip()
HTTP_TIMEOUT = float(os.environ.get("SEO_TIMEOUT", "30"))


def _headers() -> dict:
    h = {"Accept": "application/json",
         # Cloudflare 403s some default library UAs on our API hosts -- always identify.
         "User-Agent": "cyberwarex-mcp/1.0 (+https://cyberwarex.com)"}
    if X_PAYMENT:
        h["X-PAYMENT"] = X_PAYMENT
    else:
        # No payment configured -> opt into the service free trial so an evaluator's FIRST calls
        # return REAL DATA instead of a paywall. Without this the very first MCP tool call an
        # evaluator makes returns a 402 and they never see the product.
        h["X-Free-Trial"] = "1"
    if RAPIDAPI_SECRET:
        h["X-RapidAPI-Proxy-Secret"] = RAPIDAPI_SECRET
    return h


def _payment_required(r) -> str:
    try:
        body = r.json()
    except ValueError:
        body = {"error": "payment required"}
    return json.dumps({
        "x402_payment_required": True,
        "hint": "Pay the x402 invoice below (USDC on Base) and retry with an X-PAYMENT "
                "header, or set SEO_X_PAYMENT for this MCP server.",
        "invoice": body,
    }, indent=2)


def _get(path: str, params: dict) -> str:
    params = {k: v for k, v in params.items() if v is not None and v != ""}
    try:
        r = requests.get(f"{BASE_URL}{path}", headers=_headers(), params=params, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        return json.dumps({"error": "request failed", "reason": str(e)})
    if r.status_code == 402:
        return _payment_required(r)
    try:
        return json.dumps(r.json(), indent=2)
    except ValueError:
        return r.text


def _audit(a: dict) -> str:
    return _get("/audit", {"url": a.get("url")})


def _trends(a: dict) -> str:
    return _get("/trends", {"keyword": a.get("keyword"), "geo": a.get("geo", ""),
                            "timeframe": a.get("timeframe", "90d")})


def _meta(a: dict) -> str:
    return _get("/meta", {"url": a.get("url")})


TOOLS = [
    types.Tool(
        name="seo_audit",
        description="Run a full on-page SEO audit on a URL: meta tags, headings, image alt coverage, "
                    "schema.org presence, canonical, and other actionable on-page fields. $0.01/call.",
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string", "description": "Page URL to audit"},
        }},
    ),
    types.Tool(
        name="seo_trends",
        description="Keyword search-interest trend over time (optionally by geography). $0.02/call.",
        inputSchema={"type": "object", "required": ["keyword"], "properties": {
            "keyword": {"type": "string", "description": "Keyword or phrase"},
            "geo": {"type": "string", "description": "Country code, e.g. US (blank = worldwide)", "default": ""},
            "timeframe": {"type": "string", "description": "e.g. 90d, 12m", "default": "90d"},
        }},
    ),
    types.Tool(
        name="seo_meta",
        description="Free peek at a page's title, meta description and canonical URL. No payment required.",
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string", "description": "Page URL"},
        }},
    ),
]

_DISPATCH = {
    "seo_audit": _audit,
    "seo_trends": _trends,
    "seo_meta": _meta,
}


def _dispatch(name: str, args: dict) -> str:
    fn = _DISPATCH.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool: {name}"})
    return fn(args or {})


async def on_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOLS)


async def on_call_tool(ctx, params) -> types.CallToolResult:
    out = await asyncio.to_thread(_dispatch, params.name, params.arguments or {})
    return types.CallToolResult(content=[types.TextContent(type="text", text=out)])


server = Server(
    "x402-seo-audit",
    on_list_tools=on_list_tools,
    on_call_tool=on_call_tool,
)


async def _main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
