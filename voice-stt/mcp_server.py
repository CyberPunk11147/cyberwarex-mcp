"""x402 Voice-to-Text — MCP server (stdio).

Exposes the Voice-to-Text service as MCP tools so any MCP-speaking agent (Claude Desktop,
Bedrock AgentCore, Cursor, etc.) can transcribe voice messages:

    voice_transcribe(audio_base64 | audio_path, filename) -> transcript text

It is a thin, stateless proxy to the HTTP service (default https://voice.cyberwarex.com, override with
VOICE_BASE_URL — set this to the public funnel URL for remote agents).

Payment model: /transcribe is x402-gated. This MCP server forwards an `X-PAYMENT` header when the
caller provides one via the VOICE_X_PAYMENT env var, and otherwise surfaces the 402 payment
requirement (the full `accepts` + `extensions.bazaar` block) back to the agent so *its* x402 client
can pay and retry. Payment stays on the calling agent's wallet — this server never holds keys. For
unattended use, set VOICE_RAPIDAPI_SECRET (bypass) or run the service in placeholder mode.

Billing is per 10-second block ($0.015/block), quoted per the actual clip length; clips over the
service cap (STT_MAX_SECONDS, default 10 min) are refused before any charge.

Run:  python mcp_server.py       (speaks MCP over stdio)
"""
from __future__ import annotations

import asyncio
import base64
import json
import os

import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

BASE_URL = (os.environ.get("VOICE_BASE_URL") or "https://voice.cyberwarex.com").rstrip("/")
X_PAYMENT = os.environ.get("VOICE_X_PAYMENT", "").strip()
RAPIDAPI_SECRET = os.environ.get("VOICE_RAPIDAPI_SECRET", "").strip()
HTTP_TIMEOUT = float(os.environ.get("VOICE_TIMEOUT", "180"))


def _headers() -> dict:
    h = {"Accept": "application/json",
         # Cloudflare 403s some default library UAs on our API hosts — always identify.
         "User-Agent": "cyberwarex-mcp/1.0 (+https://cyberwarex.com)"}
    if X_PAYMENT:
        h["X-PAYMENT"] = X_PAYMENT
    else:
        # No payment configured -> opt into the service free trial so an evaluator\'s
        # FIRST calls return REAL DATA instead of a paywall. Without this the very first
        # MCP tool call an evaluator makes returns a 402 and they never see the product.
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
                "header, or set VOICE_X_PAYMENT for this MCP server.",
        "invoice": body,
    }, indent=2)


def _audio_bytes(args: dict) -> tuple:
    """Resolve audio bytes + a filename from either base64 or a local path. Returns (bytes, filename)
    or (None, error_json)."""
    fn = args.get("filename") or "audio.wav"
    b64 = args.get("audio_base64")
    if b64:
        try:
            return base64.b64decode(b64), fn
        except Exception as e:
            return None, json.dumps({"error": "invalid audio_base64", "reason": str(e)})
    path = args.get("audio_path")
    if path:
        try:
            with open(path, "rb") as f:
                return f.read(), os.path.basename(path) or fn
        except Exception as e:
            return None, json.dumps({"error": "cannot read audio_path", "reason": str(e)})
    return None, json.dumps({"error": "provide audio_base64 or audio_path"})


def _transcribe(args: dict) -> str:
    audio, fn = _audio_bytes(args)
    if audio is None:
        return fn  # error json
    try:
        r = requests.post(f"{BASE_URL}/transcribe", headers=_headers(),
                          files={"audio": (fn, audio)}, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        return json.dumps({"error": "request failed", "reason": str(e)})
    if r.status_code == 402:
        return _payment_required(r)
    try:
        return json.dumps(r.json(), indent=2)
    except ValueError:
        return r.text


TOOLS = [
    types.Tool(
        name="voice_transcribe",
        description="Transcribe a voice message (wav/mp3/ogg/opus) to text. Provide the audio as "
                    "base64 (audio_base64) or a local file path (audio_path). Billed per 10s block "
                    "via x402 (USDC on Base); clips over the service cap are refused before charge.",
        inputSchema={
            "type": "object",
            "properties": {
                "audio_base64": {"type": "string", "description": "Base64-encoded audio bytes"},
                "audio_path": {"type": "string", "description": "Local path to an audio file (server-side)"},
                "filename": {"type": "string", "description": "Original filename/extension hint, e.g. note.ogg"},
            },
        },
    ),
]


def _dispatch(name: str, args: dict) -> str:
    args = args or {}
    if name == "voice_transcribe":
        return _transcribe(args)
    return json.dumps({"error": f"unknown tool: {name}"})


async def on_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOLS)


async def on_call_tool(ctx, params) -> types.CallToolResult:
    out = await asyncio.to_thread(_dispatch, params.name, params.arguments or {})
    return types.CallToolResult(content=[types.TextContent(type="text", text=out)])


server = Server(
    "x402-voice-stt",
    on_list_tools=on_list_tools,
    on_call_tool=on_call_tool,
)


async def _main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
