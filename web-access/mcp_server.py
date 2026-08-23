"""Agent Web-Access — MCP server (stdio).

Exposes the four web-access capabilities as MCP tools so any MCP-speaking agent
(Claude Desktop, Bedrock AgentCore, Cursor, etc.) can call them:

    web_fetch(url, format)       -> rendered page as markdown/text/html
    web_extract(url, selector)   -> elements matching a CSS selector
    web_screenshot(url, ...)     -> PNG (base64)
    web_pdf(url)                 -> PDF (base64)

It is a thin, stateless proxy to the HTTP service (default https://web.cyberwarex.com,
override with AWA_BASE_URL — set this to the public funnel URL for remote agents).

Payment model: the HTTP service is x402-gated. This MCP server forwards an
`X-PAYMENT` header when the caller provides one via the AWA_X_PAYMENT env var, and
otherwise surfaces the 402 payment requirement (the full `accepts` + `extensions.bazaar`
block) back to the agent so *its* x402 client can pay and retry. That keeps payment
on the calling agent's wallet — this server never holds keys. For unattended use,
set AWA_RAPIDAPI_SECRET (bypass) or run the service in placeholder mode.

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

BASE_URL = (os.environ.get("AWA_BASE_URL") or "https://web.cyberwarex.com").rstrip("/")
X_PAYMENT = os.environ.get("AWA_X_PAYMENT", "").strip()
RAPIDAPI_SECRET = os.environ.get("AWA_RAPIDAPI_SECRET", "").strip()
HTTP_TIMEOUT = float(os.environ.get("AWA_TIMEOUT", "60"))

def _headers() -> dict:
    h = {"Accept": "application/json"}
    if X_PAYMENT:
        h["X-PAYMENT"] = X_PAYMENT
    if RAPIDAPI_SECRET:
        h["X-RapidAPI-Proxy-Secret"] = RAPIDAPI_SECRET
    return h


def _call(path: str, params: dict) -> str:
    """Call the HTTP service; return a text payload for the agent.

    On 402, return the payment requirement (so the agent's x402 client can pay).
    """
    try:
        r = requests.get(f"{BASE_URL}{path}", params=params, headers=_headers(),
                         timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        return json.dumps({"error": "request failed", "reason": str(e)})
    if r.status_code == 402:
        try:
            body = r.json()
        except ValueError:
            body = {"error": "payment required"}
        return json.dumps({
            "x402_payment_required": True,
            "hint": "Pay the x402 invoice below (USDC on Base) and retry with an "
                    "X-PAYMENT header, or set AWA_X_PAYMENT for this MCP server.",
            "invoice": body,
        }, indent=2)
    try:
        return json.dumps(r.json(), indent=2)
    except ValueError:
        return r.text


TOOLS = [
    types.Tool(
        name="web_fetch",
        description="Render a live web page (JavaScript executed) and return clean "
                    "content. Use this to read pages an LLM can't reach natively.",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Public http(s) URL to render"},
                "format": {"type": "string", "enum": ["markdown", "text", "html"],
                           "default": "markdown"},
            },
            "required": ["url"],
        },
    ),
    types.Tool(
        name="web_extract",
        description="Return text + attributes of elements matching a CSS selector on a "
                    "fully rendered page.",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "selector": {"type": "string", "description": "CSS selector"},
            },
            "required": ["url", "selector"],
        },
    ),
    types.Tool(
        name="web_screenshot",
        description="Render a live web page and return a PNG screenshot (base64).",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "fullpage": {"type": "boolean", "default": False},
                "width": {"type": "integer", "default": 1280},
            },
            "required": ["url"],
        },
    ),
    types.Tool(
        name="web_pdf",
        description="Render a live web page and return it as a PDF (base64).",
        inputSchema={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    ),
]


def _dispatch(name: str, args: dict) -> str:
    args = args or {}
    if name == "web_fetch":
        return _call("/fetch", {"url": args.get("url"),
                                "format": args.get("format", "markdown")})
    if name == "web_extract":
        return _call("/extract", {"url": args.get("url"),
                                  "selector": args.get("selector")})
    if name == "web_screenshot":
        return _call("/screenshot", {"url": args.get("url"),
                                     "fullpage": str(args.get("fullpage", False)).lower(),
                                     "width": args.get("width", 1280)})
    if name == "web_pdf":
        return _call("/pdf", {"url": args.get("url")})
    return json.dumps({"error": f"unknown tool: {name}"})


async def on_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOLS)


async def on_call_tool(ctx, params) -> types.CallToolResult:
    out = await asyncio.to_thread(_dispatch, params.name, params.arguments or {})
    return types.CallToolResult(content=[types.TextContent(type="text", text=out)])


server = Server(
    "agent-web-access",
    on_list_tools=on_list_tools,
    on_call_tool=on_call_tool,
)


async def _main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
