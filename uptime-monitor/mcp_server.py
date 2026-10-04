"""x402 Uptime Monitor -- MCP server (stdio).

Exposes the Uptime Monitor service (uptime.cyberwarex.com) as MCP tools: independent,
continuously-measured reliability data for a fleet of endpoints.

    uptime_stats(window_days) -> fleet-wide uptime%, avg response time, check count over 1/7/30d
    uptime_history(name, days) -> raw check history for one endpoint (timestamp, status, up/down)
    uptime_sla(name, days) -> SLA report for one endpoint: uptime%, downtime periods, mean/p95/p99

It is a thin, stateless proxy to the HTTP service (default https://uptime.cyberwarex.com, override
with UPTIME_BASE_URL -- set this to the public funnel URL for remote agents).

Payment model: /stats, /endpoints/<name>/history and /endpoints/<name>/sla are x402-gated
($0.002/call each). This MCP server forwards an X-PAYMENT header when the caller provides one via
the UPTIME_X_PAYMENT env var, and otherwise surfaces the 402 payment requirement (the full
`accepts` + `extensions.bazaar` block) back to the agent so *its* x402 client can pay and retry.
Payment stays on the calling agent's wallet -- this server never holds keys.

Run:  python mcp_server.py       (speaks MCP over stdio)
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.parse

import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

BASE_URL = (os.environ.get("UPTIME_BASE_URL") or "https://uptime.cyberwarex.com").rstrip("/")
X_PAYMENT = os.environ.get("UPTIME_X_PAYMENT", "").strip()
HTTP_TIMEOUT = float(os.environ.get("UPTIME_TIMEOUT", "30"))


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
                "header, or set UPTIME_X_PAYMENT for this MCP server.",
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


def _stats(a: dict) -> str:
    return _get("/stats", {"window_days": a.get("window_days")})


def _history(a: dict) -> str:
    name = urllib.parse.quote(str(a.get("name") or ""), safe="")
    return _get(f"/endpoints/{name}/history", {"days": a.get("days", 30)})


def _sla(a: dict) -> str:
    name = urllib.parse.quote(str(a.get("name") or ""), safe="")
    return _get(f"/endpoints/{name}/sla", {"days": a.get("days", 30)})


TOOLS = [
    types.Tool(
        name="uptime_stats",
        description="Fleet-wide uptime statistics for every monitored endpoint: uptime percentage, "
                    "average response time, and total check count over 1, 7 and 30 day windows. "
                    "$0.002/call.",
        inputSchema={"type": "object", "properties": {
            "window_days": {"type": "integer", "minimum": 1,
                            "description": "Optional extra aggregation window in days."},
        }},
    ),
    types.Tool(
        name="uptime_history",
        description="Raw check history for one monitored endpoint: every probe with timestamp, "
                    "HTTP status, response time, and up/down verdict. $0.002/call.",
        inputSchema={"type": "object", "required": ["name"], "properties": {
            "name": {"type": "string", "description": "Monitored endpoint name."},
            "days": {"type": "integer", "minimum": 1, "default": 30, "description": "History window in days."},
        }},
    ),
    types.Tool(
        name="uptime_sla",
        description="SLA report for one monitored endpoint: uptime percentage, downtime periods, "
                    "mean/p95/p99 response times, and total checks. $0.002/call.",
        inputSchema={"type": "object", "required": ["name"], "properties": {
            "name": {"type": "string", "description": "Monitored endpoint name."},
            "days": {"type": "integer", "minimum": 1, "default": 30, "description": "SLA window in days."},
        }},
    ),
]

_DISPATCH = {
    "uptime_stats": _stats,
    "uptime_history": _history,
    "uptime_sla": _sla,
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
    "x402-uptime-monitor",
    on_list_tools=on_list_tools,
    on_call_tool=on_call_tool,
)


async def _main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
