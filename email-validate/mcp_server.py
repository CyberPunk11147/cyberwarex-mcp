"""x402 Email Validation -- MCP server (stdio).

Exposes the Email Validation service (validate.cyberwarex.com) as MCP tools: syntax/MX/
deliverability checks for a single address, or a batch of up to 100.

    email_validate(email) -> valid/deliverable/mx_records/mx_provider/disposable/role_account/
                              free_provider/did_you_mean/score
    email_validate_batch(emails) -> array of the same report, one per address (flat $0.05/batch)

No SMTP probe, no API key, no signup. It is a thin, stateless proxy to the HTTP service (default
https://validate.cyberwarex.com, override with VALIDATE_BASE_URL -- set this to the public funnel
URL for remote agents).

Payment model: /validate ($0.005/call) and /validate-batch ($0.05/batch) are x402-gated. This MCP
server forwards an X-PAYMENT header when the caller provides one via the VALIDATE_X_PAYMENT env
var, and otherwise surfaces the 402 payment requirement (the full `accepts` + `extensions.bazaar`
block) back to the agent so *its* x402 client can pay and retry. Payment stays on the calling
agent's wallet -- this server never holds keys.

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

BASE_URL = (os.environ.get("VALIDATE_BASE_URL") or "https://validate.cyberwarex.com").rstrip("/")
X_PAYMENT = os.environ.get("VALIDATE_X_PAYMENT", "").strip()
HTTP_TIMEOUT = float(os.environ.get("VALIDATE_TIMEOUT", "30"))


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
                "header, or set VALIDATE_X_PAYMENT for this MCP server.",
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


def _post(path: str, json_body) -> str:
    try:
        r = requests.post(f"{BASE_URL}{path}", headers=_headers(), json=json_body, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        return json.dumps({"error": "request failed", "reason": str(e)})
    if r.status_code == 402:
        return _payment_required(r)
    try:
        return json.dumps(r.json(), indent=2)
    except ValueError:
        return r.text


def _validate(a: dict) -> str:
    return _get("/validate", {"email": a.get("email")})


def _validate_batch(a: dict) -> str:
    return _post("/validate-batch", {"emails": a.get("emails") or []})


TOOLS = [
    types.Tool(
        name="email_validate",
        description="Validate one email address: syntax, MX deliverability, provider fingerprint, "
                    "disposable/role/free-provider flags, a did-you-mean typo suggestion, and a "
                    "quality score. No SMTP probe. $0.005/call.",
        inputSchema={"type": "object", "required": ["email"], "properties": {
            "email": {"type": "string", "description": "Email address to validate."},
        }},
    ),
    types.Tool(
        name="email_validate_batch",
        description="Validate up to 100 email addresses in one call, same report per address. Flat "
                    "$0.05/batch regardless of count.",
        inputSchema={"type": "object", "required": ["emails"], "properties": {
            "emails": {"type": "array", "items": {"type": "string"},
                      "description": "Up to 100 email addresses."},
        }},
    ),
]

_DISPATCH = {
    "email_validate": _validate,
    "email_validate_batch": _validate_batch,
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
    "x402-email-validate",
    on_list_tools=on_list_tools,
    on_call_tool=on_call_tool,
)


async def _main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
