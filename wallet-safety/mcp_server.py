"""CyberWareX Wallet Safety — MCP server (stdio).

Pre-sign / pre-pay safety toolkit for wallet, trading and payment agents. Six tools, six live
x402 services (USDC on Base, no account, no API key):

    simulate_transaction(chain, to, data, value, from)  -> decode + danger flags + eth_call would-succeed preview
    decode_calldata(data, to)                           -> named function + args + risk flags (no simulation, cheaper)
    decode_signature_request(typed_data | message)      -> EIP-712 / personal_sign -> plain summary + drainer flags
    address_risk(address, chain, deep)                  -> OFAC sanctions + taint (+ scam-deployer ledger) -> BLOCK/REVIEW/ALLOW
    contract_abi(address, chain)                        -> verified ABI + function/event signatures + proxy detection
    url_safety(url)                                     -> phishing-risk score -> SAFE/SUSPICIOUS/DANGEROUS + reasons

Thin, stateless proxy to the HTTP services. Payment model: every service is x402-gated. This server
forwards an X-PAYMENT header when the caller provides one via CWX_X_PAYMENT, and otherwise opts into
each service's free trial (3 calls/day per IP) so an evaluator's first calls return real data; once the
trial is spent the 402 invoice is surfaced back to the agent so ITS x402 client can pay and retry.
The server holds no keys.

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

X_PAYMENT = os.environ.get("CWX_X_PAYMENT", "").strip()
HTTP_TIMEOUT = float(os.environ.get("CWX_TIMEOUT", "60"))
URLS = {
    "simulate": (os.environ.get("CWX_SIMULATE_URL") or "https://simulate.cyberwarex.com").rstrip("/"),
    "sign": (os.environ.get("CWX_SIGN_URL") or "https://sign.cyberwarex.com").rstrip("/"),
    "sanctions": (os.environ.get("CWX_SANCTIONS_URL") or "https://sanctions.cyberwarex.com").rstrip("/"),
    "abi": (os.environ.get("CWX_ABI_URL") or "https://abi.cyberwarex.com").rstrip("/"),
    "safe": (os.environ.get("CWX_SAFE_URL") or "https://safe.cyberwarex.com").rstrip("/"),
}
CHAINS6 = ["base", "bsc", "ethereum", "polygon", "arbitrum", "optimism"]


def _headers() -> dict:
    h = {"Accept": "application/json",
         # Cloudflare 403s some default library UAs on our API hosts — always identify.
         "User-Agent": "cyberwarex-mcp/1.0 (+https://cyberwarex.com)"}
    if X_PAYMENT:
        h["X-PAYMENT"] = X_PAYMENT
    else:
        h["X-Free-Trial"] = "1"
    return h


def _get(service: str, path: str, params: dict) -> str:
    params = {k: v for k, v in (params or {}).items() if v not in (None, "", [])}
    try:
        r = requests.get(f"{URLS[service]}{path}", params=params, headers=_headers(), timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        return json.dumps({"error": "request failed", "reason": str(e)})
    if r.status_code == 402:
        try:
            body = r.json()
        except ValueError:
            body = {"error": "payment required"}
        return json.dumps({
            "x402_payment_required": True,
            "hint": "Free trial spent or not available. Pay the x402 invoice below (USDC on Base) and retry "
                    "with an X-PAYMENT header, or set CWX_X_PAYMENT for this MCP server.",
            "invoice": body,
        }, indent=2)
    try:
        return json.dumps(r.json(), indent=2)
    except ValueError:
        return r.text


def _chain_prop(desc="Chain: base (default), bsc, ethereum, polygon, arbitrum, optimism"):
    return {"type": "string", "enum": CHAINS6, "default": "base", "description": desc}


TOOLS = [
    types.Tool(
        name="simulate_transaction",
        description="Simulate an unsigned EVM transaction BEFORE signing it: decodes the function and arguments, "
                    "flags dangerous actions (unlimited token approvals, setApprovalForAll, ownership transfers, "
                    "native-value drains) and runs a keyless eth_call preview that says whether it would succeed or "
                    "revert. Base, BSC, Ethereum, Polygon, Arbitrum, Optimism. Deterministic, read-only. Call this on "
                    "every transaction a wallet or trading agent is about to sign.",
        inputSchema={"type": "object", "properties": {
            "to": {"type": "string", "description": "Destination address (0x…)"},
            "data": {"type": "string", "description": "Hex calldata (0x…), empty for a plain value transfer"},
            "value": {"type": "string", "description": "Native value in wei as a decimal or hex string (default 0)"},
            "from": {"type": "string", "description": "Sender address used for the eth_call preview (optional)"},
            "chain": _chain_prop()}, "required": ["to"]}),
    types.Tool(
        name="decode_calldata",
        description="Decode raw EVM calldata into a named function, signature and arguments plus the same security "
                    "risk flags as simulate_transaction, without running the simulation (cheaper). Use when you only "
                    "need to know WHAT a transaction does.",
        inputSchema={"type": "object", "properties": {
            "data": {"type": "string", "description": "Hex calldata (0x…)"},
            "to": {"type": "string", "description": "Target contract address, improves decoding (optional)"}},
            "required": ["data"]}),
    types.Tool(
        name="decode_signature_request",
        description="Decode an off-chain SIGNATURE REQUEST (EIP-712 typed data or a personal_sign message) into a plain "
                    "summary with risk level and drainer flags: Permit / Permit2 / DAI permit token approvals, Seaport "
                    "and other marketplace listings, unlimited amounts, far-future deadlines, unknown spenders. Call "
                    "this before signing anything a dapp asks for.",
        inputSchema={"type": "object", "properties": {
            "typed_data": {"type": "string", "description": "EIP-712 typed data as a JSON string (domain, types, primaryType, message)"},
            "message": {"type": "string", "description": "personal_sign message text or hex (use instead of typed_data)"}}}),
    types.Tool(
        name="address_risk",
        description="Screen an EVM wallet or contract address BEFORE paying or interacting with it: OFAC sanctions "
                    "lists, taint from known-bad addresses and (deep=true) CyberWareX's own scam/rug deployer ledger. "
                    "Returns BLOCK / REVIEW / ALLOW with reasons and a 0-100 risk score.",
        inputSchema={"type": "object", "properties": {
            "address": {"type": "string", "description": "Wallet or contract address (0x…)"},
            "chain": _chain_prop(),
            "deep": {"type": "boolean", "default": False,
                     "description": "true = full risk report incl. scam-deployer ledger (/risk); false = sanctions + taint screen only (/screen, cheaper)"}},
            "required": ["address"]}),
    types.Tool(
        name="contract_abi",
        description="Fetch a verified contract's ABI keylessly (from Sourcify): full ABI, function and event "
                    "signatures/selectors, contract name and proxy detection (implementation address). Use it to "
                    "encode calls or to understand what a contract can do.",
        inputSchema={"type": "object", "properties": {
            "address": {"type": "string", "description": "Contract address (0x…)"},
            "chain": _chain_prop()}, "required": ["address"]}),
    types.Tool(
        name="url_safety",
        description="Score a URL for phishing risk before an agent opens, trusts or sends funds through it: "
                    "typosquats of known brands, homoglyph/punycode domains, credentials in the URL, raw IP hosts, "
                    "risky TLDs, suspicious subdomain depth and domain age (RDAP). Returns SAFE / SUSPICIOUS / "
                    "DANGEROUS with a 0-100 score and reasons.",
        inputSchema={"type": "object", "properties": {
            "url": {"type": "string", "description": "Full URL to check (https://…)"}}, "required": ["url"]}),
]


def _dispatch(name: str, args: dict) -> str:
    a = args or {}
    if name == "simulate_transaction":
        return _get("simulate", "/simulate", {"chain": a.get("chain", "base"), "to": a.get("to"), "data": a.get("data"),
                                              "value": a.get("value"), "from": a.get("from")})
    if name == "decode_calldata":
        return _get("simulate", "/decode", {"data": a.get("data"), "to": a.get("to")})
    if name == "decode_signature_request":
        td = a.get("typed_data")
        if isinstance(td, (dict, list)):
            td = json.dumps(td)
        return _get("sign", "/decode", {"typed_data": td, "message": a.get("message")})
    if name == "address_risk":
        path = "/risk" if a.get("deep") else "/screen"
        return _get("sanctions", path, {"address": a.get("address"), "chain": a.get("chain", "base")})
    if name == "contract_abi":
        return _get("abi", "/abi", {"address": a.get("address"), "chain": a.get("chain", "base")})
    if name == "url_safety":
        return _get("safe", "/check", {"url": a.get("url")})
    return json.dumps({"error": f"unknown tool: {name}"})


async def on_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOLS)


async def on_call_tool(ctx, params) -> types.CallToolResult:
    out = await asyncio.to_thread(_dispatch, params.name, params.arguments or {})
    return types.CallToolResult(content=[types.TextContent(type="text", text=out)])


server = Server("cyberwarex-wallet-safety", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def _main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
