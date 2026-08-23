"""DeFi Safety Oracle — MCP server (stdio).

Exposes the oracle's Base + BSC token risk checks as MCP tools so any MCP-speaking agent can call them:

    token_safety(address, chain)    -> full safety report (grade, honeypot, tradeable, flags+evidence)
    honeypot_check(address, chain)  -> focused buy/sell simulation (is_honeypot, taxes)
    contract_risk(address, chain)   -> contract powers (verified, proxy, owner, mint/pause/blacklist)

Thin, stateless proxy to the HTTP service (default the public funnel URL; override with DSO_BASE_URL).
Payment model: the service is x402-gated. This server forwards an X-PAYMENT header when the caller
provides one via DSO_X_PAYMENT, and otherwise surfaces the 402 invoice back to the agent so ITS x402
client can pay and retry. The server holds no keys.

Run:  python mcp_server.py   (speaks MCP over stdio)
"""
from __future__ import annotations

import asyncio
import json
import os

import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

BASE_URL = (os.environ.get("DSO_BASE_URL") or "https://oracle.cyberwarex.com").rstrip("/")
X_PAYMENT = os.environ.get("DSO_X_PAYMENT", "").strip()
RAPIDAPI_SECRET = os.environ.get("DSO_RAPIDAPI_SECRET", "").strip()
HTTP_TIMEOUT = float(os.environ.get("DSO_TIMEOUT", "60"))


def _headers() -> dict:
    h = {"Accept": "application/json"}
    if X_PAYMENT:
        h["X-PAYMENT"] = X_PAYMENT
    if RAPIDAPI_SECRET:
        h["X-RapidAPI-Proxy-Secret"] = RAPIDAPI_SECRET
    return h


def _call(path: str, address: str, chain="base") -> str:
    try:
        r = requests.get(f"{BASE_URL}{path}", params={"address": address, "chain": chain},
                         headers=_headers(), timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        return json.dumps({"error": "request failed", "reason": str(e)})
    if r.status_code == 402:
        try:
            body = r.json()
        except ValueError:
            body = {"error": "payment required"}
        return json.dumps({
            "x402_payment_required": True,
            "hint": "Pay the x402 invoice below (USDC on Base) and retry with an X-PAYMENT header, "
                    "or set DSO_X_PAYMENT for this MCP server.",
            "invoice": body,
        }, indent=2)
    try:
        return json.dumps(r.json(), indent=2)
    except ValueError:
        return r.text


_ADDR = {"type": "object", "properties": {"address": {"type": "string",
         "description": "Base or BSC ERC-20 token/contract address (0x…)"},
         "chain": {"type": "string", "enum": ["base", "bsc"], "default": "base",
         "description": "Chain to query: base or bsc (default: base)"}},
         "required": ["address"]}

TOOLS = [
    types.Tool(name="token_safety", inputSchema=_ADDR,
               description="Full Base + BSC (pass chain=base|bsc) token safety report: honeypot/tax simulation "
                           "(Aerodrome+Uniswap V3), contract powers, ownership, GoPlus/honeypot.is cross-check. Returns grade "
                           "A–F, risk_score, is_honeypot, tradeable, and flags with on-chain evidence. Call BEFORE trading "
                           "a token."),
    types.Tool(name="honeypot_check", inputSchema=_ADDR,
               description="Focused honeypot check: live buy-then-sell simulation on Base + BSC (pass chain=base|bsc) — "
                           "is_honeypot, buy/sell success, round-trip loss."),
    types.Tool(name="contract_risk", inputSchema=_ADDR,
               description="Contract-level risk on Base + BSC (pass chain=base|bsc): verified source, upgradeable proxy + "
                           "admin, ownership/renounce, mint/pause/blacklist/fee powers."),
]


def _dispatch(name: str, args: dict) -> str:
    a = (args or {}).get("address", "")
    chain = (args or {}).get("chain", "base")
    if name == "token_safety":
        return _call("/token", a, chain)
    if name == "honeypot_check":
        return _call("/honeypot", a, chain)
    if name == "contract_risk":
        return _call("/contract", a, chain)
    return json.dumps({"error": f"unknown tool: {name}"})


async def on_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOLS)


async def on_call_tool(ctx, params) -> types.CallToolResult:
    out = await asyncio.to_thread(_dispatch, params.name, params.arguments or {})
    return types.CallToolResult(content=[types.TextContent(type="text", text=out)])


server = Server("defi-safety-oracle", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def _main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
