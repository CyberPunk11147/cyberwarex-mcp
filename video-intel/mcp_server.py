"""x402 Video Intelligence -- MCP server (stdio).

Exposes the Video Intelligence service (video.cyberwarex.com) as MCP tools: YouTube transcripts and
metadata, never downloading media so it stays fast and light.

    video_transcript(url, lang, format) -> timed caption segments + full plain text (or SRT)
    video_meta(url) -> title, channel, duration, upload date, view/like counts, chapters, captions

It is a thin, stateless proxy to the HTTP service (default https://video.cyberwarex.com, override
with VIDEO_BASE_URL -- set this to the public funnel URL for remote agents).

Payment model: /transcript ($0.003/call) and /meta ($0.001/call) are x402-gated. This MCP server
forwards an X-PAYMENT header when the caller provides one via the VIDEO_X_PAYMENT env var, and
otherwise surfaces the 402 payment requirement (the full `accepts` + `extensions.bazaar` block)
back to the agent so *its* x402 client can pay and retry. Payment stays on the calling agent's
wallet -- this server never holds keys.

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

BASE_URL = (os.environ.get("VIDEO_BASE_URL") or "https://video.cyberwarex.com").rstrip("/")
X_PAYMENT = os.environ.get("VIDEO_X_PAYMENT", "").strip()
HTTP_TIMEOUT = float(os.environ.get("VIDEO_TIMEOUT", "30"))


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
    return h


def _payment_required(r) -> str:
    try:
        body = r.json()
    except ValueError:
        body = {"error": "payment required"}
    return json.dumps({
        "x402_payment_required": True,
        "hint": "Pay the x402 invoice below (USDC on Base) and retry with an X-PAYMENT "
                "header, or set VIDEO_X_PAYMENT for this MCP server.",
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


def _transcript(a: dict) -> str:
    return _get("/transcript", {"url": a.get("url"), "lang": a.get("lang", "en"),
                                "format": a.get("format", "json")})


def _meta(a: dict) -> str:
    return _get("/meta", {"url": a.get("url")})


TOOLS = [
    types.Tool(
        name="video_transcript",
        description="YouTube video transcript with per-segment timing: timed caption segments "
                    "(start, duration, text), full plain text, or SRT subtitles. Uses manual "
                    "captions when available, falls back to auto captions. $0.003/call.",
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string", "description": "YouTube video URL."},
            "lang": {"type": "string", "description": "Caption language code.", "default": "en"},
            "format": {"type": "string", "description": "json or srt.", "default": "json"},
        }},
    ),
    types.Tool(
        name="video_meta",
        description="YouTube video metadata: title, channel name/id, duration, upload date, view "
                    "and like counts, description, thumbnail, chapter list, and which caption "
                    "tracks exist. Never downloads media. $0.001/call.",
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string", "description": "YouTube video URL."},
        }},
    ),
]

_DISPATCH = {
    "video_transcript": _transcript,
    "video_meta": _meta,
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
    "x402-video-intel",
    on_list_tools=on_list_tools,
    on_call_tool=on_call_tool,
)


async def _main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
