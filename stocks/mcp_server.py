"""x402 SEC Stocks -- MCP server (stdio).

Exposes the SEC Stocks service (stocks.cyberwarex.com) as MCP tools: company profile, recent
filings, and annual fundamentals for any US SEC-registered ticker -- keyless, sourced straight from
SEC EDGAR (public domain).

    stock_company(ticker) -> legal name, CIK, SIC industry, exchanges, state of incorporation, FY end
    stock_filings(ticker, form, limit) -> recent 10-K/10-Q/8-K/Form 4 filings with document URLs
    stock_fundamentals(ticker) -> revenue, net income, assets, equity, EPS from XBRL

It is a thin, stateless proxy to the HTTP service (default https://stocks.cyberwarex.com, override
with STOCKS_BASE_URL -- set this to the public funnel URL for remote agents).

Payment model: all three endpoints are x402-gated ($0.002-$0.004/call). This MCP server forwards an
X-PAYMENT header when the caller provides one via the STOCKS_X_PAYMENT env var, and otherwise
surfaces the 402 payment requirement (the full `accepts` + `extensions.bazaar` block) back to the
agent so *its* x402 client can pay and retry. Payment stays on the calling agent's wallet -- this
server never holds keys.

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

BASE_URL = (os.environ.get("STOCKS_BASE_URL") or "https://stocks.cyberwarex.com").rstrip("/")
X_PAYMENT = os.environ.get("STOCKS_X_PAYMENT", "").strip()
HTTP_TIMEOUT = float(os.environ.get("STOCKS_TIMEOUT", "30"))


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
                "header, or set STOCKS_X_PAYMENT for this MCP server.",
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


def _company(a: dict) -> str:
    return _get("/company", {"ticker": a.get("ticker")})


def _filings(a: dict) -> str:
    return _get("/filings", {"ticker": a.get("ticker"), "form": a.get("form", ""),
                             "limit": a.get("limit", 10)})


def _fundamentals(a: dict) -> str:
    return _get("/fundamentals", {"ticker": a.get("ticker")})


TOOLS = [
    types.Tool(
        name="stock_company",
        description="Company profile for a US SEC-registered ticker: legal name, CIK, SIC industry, "
                    "exchanges, all tickers, state of incorporation and fiscal year end, from SEC "
                    "EDGAR. $0.002/call.",
        inputSchema={"type": "object", "required": ["ticker"], "properties": {
            "ticker": {"type": "string", "description": "US ticker (AAPL, MSFT...) or a CIK number."},
        }},
    ),
    types.Tool(
        name="stock_filings",
        description="Recent SEC filings for a US ticker (10-K, 10-Q, 8-K, Form 4, and more): form "
                    "type, filing date, report date, accession number and a direct document URL. "
                    "$0.004/call.",
        inputSchema={"type": "object", "required": ["ticker"], "properties": {
            "ticker": {"type": "string", "description": "US ticker or CIK."},
            "form": {"type": "string", "description": "Optional form filter, e.g. 10-K, 10-Q, 8-K, 4.", "default": ""},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
        }},
    ),
    types.Tool(
        name="stock_fundamentals",
        description="Key annual fundamentals for a US ticker from XBRL: revenue, net income, assets, "
                    "equity, EPS. $0.004/call.",
        inputSchema={"type": "object", "required": ["ticker"], "properties": {
            "ticker": {"type": "string", "description": "US ticker or CIK."},
        }},
    ),
]

_DISPATCH = {
    "stock_company": _company,
    "stock_filings": _filings,
    "stock_fundamentals": _fundamentals,
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
    "x402-stocks",
    on_list_tools=on_list_tools,
    on_call_tool=on_call_tool,
)


async def _main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
