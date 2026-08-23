"""CyberWareX Onchain Query — MCP server (stdio).

Exposes the six chain-data capabilities as MCP tools so any MCP-speaking agent
(Claude Desktop/Code, Cursor, Bedrock AgentCore, etc.) can call them:

    chain_wallet(address, chain)  -> wallet snapshot (native + USDC balance, tx count, USD values)
    chain_token(address, chain)   -> ERC-20 profile + live DEX market data
    chain_price(query)            -> spot price for a symbol or token address
    chain_gas(chain)              -> current gas market
    chain_tx(hash, chain)         -> decoded transaction
    chain_ens(name)               -> ENS resolution

Chains: base, ethereum, arbitrum, optimism, polygon.

It is a thin, stateless proxy to the HTTP service (default https://chain.cyberwarex.com,
override with CHAIN_BASE_URL). Payment model: the HTTP service is x402-gated ($0.002-$0.004
per call, USDC on Base, gasless EIP-3009). This server forwards an `X-PAYMENT` header when
provided via the CHAIN_X_PAYMENT env var, and otherwise surfaces the 402 payment requirement
(the full `accepts` + `extensions.bazaar` block) back to the agent so *its* x402 client can
pay and retry. This server never holds keys.

Run:  python mcp_server.py       (speaks MCP over stdio; needs `pip install mcp requests`)
"""
from __future__ import annotations

import asyncio
import json
import os

import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

BASE_URL = (os.environ.get("CHAIN_BASE_URL") or "https://chain.cyberwarex.com").rstrip("/")
X_PAYMENT = os.environ.get("CHAIN_X_PAYMENT", "").strip()
HTTP_TIMEOUT = float(os.environ.get("CHAIN_TIMEOUT", "45"))

CHAINS = ["base", "ethereum", "arbitrum", "optimism", "polygon"]


def _headers() -> dict:
    h = {"Accept": "application/json"}
    if X_PAYMENT:
        h["X-PAYMENT"] = X_PAYMENT
    return h


def _call(path: str, params: dict) -> str:
    """Call the HTTP service; return a text payload for the agent.

    On 402, return the payment requirement so the agent's x402 client can pay and retry.
    """
    params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        r = requests.get(f"{BASE_URL}{path}", params=params, headers=_headers(),
                         timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        return json.dumps({"error": "request failed", "reason": str(e)})
    if r.status_code == 402:
        try:
            body = r.json()
        except Exception:
            body = {"error": "payment required"}
        return json.dumps({
            "x402_payment_required": True,
            "hint": "Pay the x402 invoice below (USDC on Base) and retry with an "
                    "X-PAYMENT header, or set CHAIN_X_PAYMENT for this MCP server.",
            "invoice": body,
        }, indent=2)
    try:
        return json.dumps(r.json(), indent=2)
    except Exception:
        return json.dumps({"status": r.status_code, "body": r.text[:2000]})


_CHAIN_PROP = {"type": "string", "enum": CHAINS, "description": "Target chain (default base)"}

TOOLS = [
    types.Tool(
        name="chain_wallet",
        description="Wallet snapshot on any major EVM chain: native balance, USDC balance, "
                    "transaction count, contract-or-EOA flag, USD values. $0.003 via x402.",
        inputSchema={"type": "object",
                     "properties": {"address": {"type": "string"}, "chain": _CHAIN_PROP},
                     "required": ["address"]},
    ),
    types.Tool(
        name="chain_token",
        description="ERC-20 token profile: on-chain metadata plus live DEX market data "
                    "(USD price, liquidity, 24h volume, top pair). $0.004 via x402.",
        inputSchema={"type": "object",
                     "properties": {"address": {"type": "string"}, "chain": _CHAIN_PROP},
                     "required": ["address"]},
    ),
    types.Tool(
        name="chain_price",
        description="Spot USD price for a symbol (e.g. 'eth') or token address. $0.002 via x402.",
        inputSchema={"type": "object",
                     "properties": {"query": {"type": "string",
                                              "description": "Symbol or 0x token address"}},
                     "required": ["query"]},
    ),
    types.Tool(
        name="chain_gas",
        description="Current gas market for a chain. $0.002 via x402.",
        inputSchema={"type": "object", "properties": {"chain": _CHAIN_PROP}},
    ),
    types.Tool(
        name="chain_tx",
        description="Decoded transaction by hash. $0.003 via x402.",
        inputSchema={"type": "object",
                     "properties": {"hash": {"type": "string"}, "chain": _CHAIN_PROP},
                     "required": ["hash"]},
    ),
    types.Tool(
        name="chain_ens",
        description="Resolve an ENS name to an address (and reverse records). $0.003 via x402.",
        inputSchema={"type": "object",
                     "properties": {"name": {"type": "string", "description": "e.g. vitalik.eth"}},
                     "required": ["name"]},
    ),
]

_PATHS = {"chain_wallet": "/wallet", "chain_token": "/token", "chain_price": "/price",
          "chain_gas": "/gas", "chain_tx": "/tx", "chain_ens": "/ens"}


def _dispatch(name: str, args: dict) -> str:
    path = _PATHS.get(name)
    if not path:
        return json.dumps({"error": f"unknown tool: {name}"})
    return _call(path, args or {})


async def on_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOLS)


async def on_call_tool(ctx, params) -> types.CallToolResult:
    out = await asyncio.to_thread(_dispatch, params.name, params.arguments or {})
    return types.CallToolResult(content=[types.TextContent(type="text", text=out)])


server = Server(
    "x402-chain-data",
    on_list_tools=on_list_tools,
    on_call_tool=on_call_tool,
)


async def _main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
