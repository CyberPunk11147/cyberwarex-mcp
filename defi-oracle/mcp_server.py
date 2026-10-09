"""DeFi Safety Oracle - MCP server (stdio).

Exposes the oracle's Base + BSC token risk checks as MCP tools so any MCP-speaking agent can call them:

    token_safety(address, chain)    -> full safety report (grade, honeypot, tradeable, flags+evidence)
    honeypot_check(address, chain)  -> focused buy/sell simulation (is_honeypot, taxes)
    contract_risk(address, chain)   -> contract powers (verified, proxy, owner, mint/pause/blacklist)
    wallet_identity(address, chain) -> who is behind a wallet: age, ENS, linked X, deployer history, trust
    x_identity(handle)              -> X/Twitter account profile: age, followers, verified, trust signals
    token_identity(address)         -> who is behind a token: market, socials, creator wallet, rug/impersonation signals
    identity_quick(q)               -> $0.001 taster: 0x address or @handle -> one-line trust verdict + upgrade URL

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
    h = {"Accept": "application/json",
         # Cloudflare 403s some default library UAs on our API hosts - always identify.
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


def _call(path: str, address: str, chain="base") -> str:
    return _get(path, {"address": address, "chain": chain})


def _get(path: str, params: dict) -> str:
    try:
        r = requests.get(f"{BASE_URL}{path}", params=params,
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


def _crowd(path: str, chain: str, token: str) -> str:
    """Crowd Check: get the free quote, wait until it is ready, then call the paid slice with ?quote=."""
    import time as _t
    qid = None
    for _ in range(12):
        try:
            d = requests.get(f"{BASE_URL}/v1/holders/quote", params={"chain": chain, "token": token},
                             headers={"User-Agent": _headers()["User-Agent"]}, timeout=HTTP_TIMEOUT).json()
        except (requests.RequestException, ValueError) as e:
            return json.dumps({"error": "quote failed", "reason": str(e)})
        if d.get("status") == "ready":
            qid = d.get("quote_id"); break
        if d.get("status") == "error" or d.get("error"):
            return json.dumps(d)
        _t.sleep(8)
    if not qid:
        return json.dumps({"error": "quote still computing - call again in a minute (no charge)"})
    return _get(path, {"quote": qid})


def _verdict(chain: str, token: str) -> str:
    import time as _t
    for _ in range(10):
        try:
            r = requests.get(f"{BASE_URL}/v1/holders/verdict", params={"chain": chain, "token": token},
                             headers=_headers(), timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            return json.dumps({"error": "request failed", "reason": str(e)})
        if r.status_code != 202:
            break
        _t.sleep(8)                                   # computing: not charged, the same URL is simply repeated
    else:
        return json.dumps({"error": "still computing - call again in a minute (no charge)"})
    return _get("/v1/holders/verdict", {"chain": chain, "token": token})


_CROWD = {
    "type": "object",
    "properties": {
        "token": {"type": "string",
                  "description": "The token to check: a 0x contract address (Base, Ethereum, Robinhood Chain) or a Solana mint."},
        "chain": {"type": "string", "enum": ["base", "ethereum", "robinhood", "solana"], "default": "base",
                  "description": "Chain the token lives on: base (default), ethereum, robinhood or solana."},
    },
    "required": ["token"],
}


_ADDR = {
    "type": "object",
    "properties": {
        "address": {
            "type": "string",
            "pattern": "^0x[a-fA-F0-9]{40}$",
            "description": "The ERC-20 token contract address to evaluate: a 42-character hex string starting with 0x "
                           "(for example 0x4200000000000000000000000000000000000006). This is the token you are about to "
                           "buy, approve, or receive.",
        },
        "chain": {
            "type": "string",
            "enum": ["base", "bsc"],
            "default": "base",
            "description": "Which chain the token lives on: 'base' for Base mainnet (the default) or 'bsc' for BNB Smart "
                           "Chain. It must match the network the address is deployed on.",
        },
    },
    "required": ["address"],
}

TOOLS = [
    types.Tool(
        name="token_safety",
        inputSchema=_ADDR,
        description=(
            "Decide whether an ERC-20 token is safe to trade before committing funds. Runs a live buy-then-sell "
            "simulation (Aerodrome and Uniswap V3) plus contract-power and ownership analysis and a GoPlus and "
            "honeypot.is cross-check, then returns a single verdict. "
            "Use it when an agent is about to swap into, approve, or accept a token it has not vetted. "
            "Returns JSON with: grade (A to F), risk_score (0-100), is_honeypot (boolean), tradeable (boolean), and a "
            "flags array where each flag carries on-chain evidence. Read-only: no wallet, key, or signature needed; a "
            "cold simulation can take a few seconds. Paid per call in USDC on Base via x402."
        ),
    ),
    types.Tool(
        name="honeypot_check",
        inputSchema=_ADDR,
        description=(
            "Answer one focused question: can this token actually be sold after it is bought? Executes a real "
            "buy-then-sell round trip as a simulated transaction on Base or BSC. "
            "Use it when you only need the honeypot yes-or-no, not the full safety report. "
            "Returns JSON with: is_honeypot (boolean), buy_success (boolean), sell_success (boolean), and "
            "round_trip_loss_percent (the tax or slippage lost across the round trip). Read-only: no wallet needed. "
            "Paid per call in USDC on Base via x402."
        ),
    ),
    types.Tool(
        name="contract_risk",
        inputSchema=_ADDR,
        description=(
            "Inspect what the team behind a token contract is able to do to holders, without running a trade "
            "simulation. Reports verified-source status, whether the contract is an upgradeable proxy and who its admin "
            "is, whether ownership is renounced, and which dangerous powers exist (mint, pause, blacklist, and mutable "
            "fees). "
            "Use it when you want the governance and rug-vector picture rather than the tradeability verdict. "
            "Returns JSON with each power as a boolean plus the resolved owner and admin addresses. Read-only. "
            "Paid per call in USDC on Base via x402."
        ),
    ),
    types.Tool(
        name="wallet_identity",
        inputSchema={
            "type": "object",
            "properties": {
                "address": {"type": "string", "pattern": "^0x[a-fA-F0-9]{40}$",
                            "description": "The wallet (EOA or contract) address to profile, 42-char hex starting with 0x."},
                "chain": {"type": "string", "enum": ["base", "ethereum"], "default": "base",
                          "description": "Chain to read the wallet on: 'base' (default) or 'ethereum'."},
            },
            "required": ["address"],
        },
        description=(
            "Find out who is behind a wallet before trusting it: how old it is, how active, who first funded it, "
            "its ENS/Basename and the X account linked on it, every token it deployed and how many we proved were "
            "honeypots or scams (from 130k+ graded Base tokens), rug-ring funders and address-poisoning attempts. "
            "Use it when an agent is about to send funds to, accept a deal from, or rely on an unknown address. "
            "Returns JSON with: is_contract, first_seen, age_days, tx_count, funded_by, ens, linked_x, deployer, "
            "signals (stable ids with severity), trust (high/medium/low/unknown) and a one-line summary. "
            "Not charged when the wallet has no history. Paid per call in USDC on Base via x402 ($0.02)."
        ),
    ),
    types.Tool(
        name="x_identity",
        inputSchema={
            "type": "object",
            "properties": {
                "handle": {"type": "string",
                           "description": "The X/Twitter handle to profile, with or without the leading @ (e.g. 'coinbase')."},
            },
            "required": ["handle"],
        },
        description=(
            "Profile an X/Twitter account to judge whether a project or person is real: existence, account age, "
            "follower/following/tweet counts, verified status and type, website, and any wallet addresses in the bio. "
            "Use it when a token or counterparty points at an X account as proof of legitimacy. "
            "Returns JSON with: exists, created_at, age_days, followers, verified, website, bio_addresses, signals "
            "(e.g. NEW_ACCOUNT_BIG_FOLLOWING), trust (high/medium/low/unknown) and a summary. "
            "Paid per call in USDC on Base via x402 ($0.01)."
        ),
    ),
    types.Tool(
        name="token_identity",
        inputSchema={
            "type": "object",
            "properties": {
                "address": {"type": "string", "pattern": "^0x[a-fA-F0-9]{40}$",
                            "description": "The ERC-20 token contract address on Base, 42-char hex starting with 0x."},
            },
            "required": ["address"],
        },
        description=(
            "Find out who is behind a token, not just whether it is tradeable: our safety verdict (grade, honeypot), "
            "DexScreener market (liquidity, 24h volume, age, pair), the declared X account and website with checks "
            "that the website actually links the contract and the same X, and the creator wallet (Clanker admin or "
            "on-chain deployer) profiled like wallet_identity. "
            "Use it for due diligence on a new launch before buying, listing, or promoting it. "
            "Returns JSON with: name, symbol, safety, market, socials, creator, signals (IMPERSONATION, "
            "WASH_TRADING, DEAD_TOKEN, CREATOR_SERIAL_RUGGER, CREATOR_RUG_RING, NO_SOCIALS...), credibility "
            "(high/medium/low/unknown) and a summary. Base only. Not charged when nothing could be read. "
            "Paid per call in USDC on Base via x402 ($0.05)."
        ),
    ),
    types.Tool(
        name="crowd_check_verdict",
        inputSchema=_CROWD,
        description=(
            "Should I touch this token? One go/no-go answer for trading bots and agents: verdict ok / caution / avoid, "
            "a 0-100 risk score, the top reasons, and key flags - can the liquidity be pulled, is the creator a serial "
            "launcher behind earlier rugs, what a Uniswap v4 hook may do, how much was bought in launch bundles. "
            "Works on Base, Ethereum, Robinhood Chain and Solana; fresh launches our hunt already checked answer instantly. "
            "Never charged while still computing. Paid per call in USDC on Base via x402 ($0.03)."
        ),
    ),
    types.Tool(
        name="crowd_check_creator",
        inputSchema=_CROWD,
        description=(
            "Who is behind this token? The creator wallet, the chain of wallets that funded it (followed up to 8 hops, "
            "so middle wallets do not hide the source), and that chain's record with us: how many earlier tokens it "
            "launched and how many rugged within a day. Flags serial launchers. Base, Ethereum, Robinhood Chain. "
            "Paid per call in USDC on Base via x402 ($0.02)."
        ),
    ),
    types.Tool(
        name="crowd_check_pool",
        inputSchema=_CROWD,
        description=(
            "Can this token's liquidity be pulled? Who holds the pool share (burned, an ordinary wallet, a contract, a "
            "known fake locker), and for Uniswap v4 pools what the hook may do on every trade (take a cut, set any fee, "
            "block sells) and whether it is a launch platform's shared hook or the creator's own code. "
            "Paid per call in USDC on Base via x402 ($0.02)."
        ),
    ),
    types.Tool(
        name="identity_quick",
        inputSchema={
            "type": "object",
            "properties": {
                "q": {"type": "string",
                      "description": "A 0x address (wallet or token) or an X handle / x.com URL."},
            },
            "required": ["q"],
        },
        description=(
            "The cheapest identity check: feed it a 0x address or an @handle and get back what kind of thing it "
            "is, a trust-or-credibility verdict, the top 3 red-flag signals, a one-line summary and the URL of the "
            "full endpoint to upgrade to. Uses cached full results when present, otherwise only the cheapest reads. "
            "Use it as a first pass before deciding whether a deeper wallet_identity or token_identity call is worth it. "
            "Returns JSON with: kind, trust_or_credibility, top_signals, summary, upgrade. "
            "Billable only when it answered. Paid per call in USDC on Base via x402 ($0.001)."
        ),
    ),
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
    if name == "wallet_identity":
        return _get("/v1/identity/wallet", {"address": a, "chain": (args or {}).get("chain", "base")})
    if name == "x_identity":
        return _get("/v1/identity/x", {"handle": str((args or {}).get("handle", "")).lstrip("@")})
    if name == "token_identity":
        return _get("/v1/identity/token", {"address": a, "chain": "base"})
    if name in ("crowd_check_verdict", "crowd_check_creator", "crowd_check_pool"):
        tok, ch = str((args or {}).get("token", "")).strip(), (args or {}).get("chain", "base")
        if not tok:
            return json.dumps({"error": "token is required"})
        if ch != "solana":
            tok = tok.lower()
        if name == "crowd_check_verdict":
            return _verdict(ch, tok)
        return _crowd("/v1/holders/creator" if name == "crowd_check_creator" else "/v1/holders/pool", ch, tok)
    if name == "identity_quick":
        return _get("/v1/identity/quick", {"q": (args or {}).get("q", "")})
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
