"""CyberWareX Agent Council - MCP server (stdio).

A second opinion for an agent that is about to act on one model's unearned confidence. Three tools,
one live x402 service (USDC on Base, no account, no API key):

    council(question)            -> 3 different models, one round  -> verdict + confidence + dissent
    council_deep(question)       -> 4 different models, two rounds of cross-examination
    council_grounded(question)   -> buys the evidence first (honeypot sim, sanctions screen, page
                                    content, SEC profile, web), then one analyst judges on it

Why an agent should call this: a single model answers with the same certainty whether it is right or
guessing. A panel of different models from different labs does not agree by construction, so when it
DOES agree that is information, and when it splits you find out before you act. Every response carries
`confidence` and `dissent`, which is what a calling agent should gate an irreversible step on.

Thin, stateless proxy to the HTTP service. Payment model: x402-gated. This server forwards an
X-PAYMENT header when the caller provides one via CWX_X_PAYMENT, and otherwise opts into the free
trial so an evaluator's first call returns a real verdict; once the trial is spent the 402 invoice is
surfaced back to the agent so ITS x402 client can pay and retry. The server holds no keys.

Run:  python mcp_server.py   (speaks MCP over stdio)

No em-dash or en-dash anywhere in this file (hard operator rule): plain hyphens only.
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
SUB_KEY = os.environ.get("CWX_SUBSCRIPTION_KEY", "").strip()
HTTP_TIMEOUT = float(os.environ.get("CWX_TIMEOUT", "120"))   # a deep council takes ~7s, grounded ~16s
BASE = (os.environ.get("CWX_COUNCIL_URL") or "https://council.cyberwarex.com").rstrip("/")
MAX_Q = 2000


def _headers() -> dict:
    h = {"Accept": "application/json",
         # Cloudflare 403s some default library UAs on our API hosts, so always identify.
         "User-Agent": "cyberwarex-mcp/1.0 (+https://cyberwarex.com)"}
    if X_PAYMENT:
        h["X-PAYMENT"] = X_PAYMENT
    elif SUB_KEY:
        h["X-Subscription-Key"] = SUB_KEY
    else:
        h["X-Free-Trial"] = "1"
    return h


def _ask(path: str, question: str) -> str:
    q = (question or "").strip()
    if not q:
        return json.dumps({"error": "question is required"})
    if len(q) > MAX_Q:
        return json.dumps({"error": f"question too long (max {MAX_Q} characters)"})
    try:
        r = requests.get(f"{BASE}{path}", params={"q": q}, headers=_headers(), timeout=HTTP_TIMEOUT)
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
                    "with an X-PAYMENT header, or set CWX_X_PAYMENT / CWX_SUBSCRIPTION_KEY for this MCP server. "
                    "For the grounded tier the invoice is priced from YOUR question and itemised under `pricing`.",
            "invoice": body,
        }, indent=2)
    if r.status_code == 503:
        # The council refuses to bill for a non-answer: fewer than two usable seats, or no evidence
        # found for a grounded call, returns 503 and settles nothing.
        try:
            return json.dumps(r.json(), indent=2)
        except ValueError:
            return json.dumps({"error": "council degraded", "charged": False})
    try:
        return json.dumps(r.json(), indent=2)
    except ValueError:
        return r.text


_Q = {"type": "string", "maxLength": MAX_Q,
      "description": "The question to put to the council. A decision question works best: include the "
                     "options and the constraints you are weighing, in one or two sentences."}

TOOLS = [
    types.Tool(
        name="council",
        description="Get a second opinion from 3 DIFFERENT AI models before acting on a judgement call. Each model "
                    "answers your question independently, then a chair returns one verdict with a confidence score, "
                    "the points every model agreed on, the dissent, and what would change the answer. Use it when the "
                    "next step depends on an opinion rather than a fact: is this safe, is it worth it, which option. "
                    "Gate an irreversible action on `confidence` and `dissent` rather than on one model's certainty. "
                    "Costs $0.01 in USDC per call over x402, no account.",
        inputSchema={"type": "object", "properties": {"question": _Q}, "required": ["question"]}),
    types.Tool(
        name="council_deep",
        description="The same panel with 4 models and two rounds: every seat sees the other seats' positions and "
                    "revises, so you get a verdict that survived cross-examination plus each seat's "
                    "changed_after_debate flag and strongest objection. Use it when being wrong is expensive, or when "
                    "a `council` call came back with low confidence or real dissent. $0.03 in USDC per call.",
        inputSchema={"type": "object", "properties": {"question": _Q}, "required": ["question"]}),
    types.Tool(
        name="council_grounded",
        description="Deliberation on bought FACTS rather than on model recollection. Your question is scanned for a "
                    "contract address, a URL or a $TICKER; the matching evidence is purchased (honeypot and "
                    "owner-powers simulation, OFAC sanctions screen, the actual page content, SEC company profile, "
                    "open web results), handed to one analyst that judges on what came back. The response lists "
                    "every source consulted and exactly what was spent on your behalf. Use it before accepting an "
                    "unknown token, counterparty, link or ticker. Priced per question, $0.05 to $0.12 in USDC: call it "
                    "unpaid first to get the itemised quote for free.",
        inputSchema={"type": "object", "properties": {"question": _Q}, "required": ["question"]}),
]


def _dispatch(name: str, a: dict) -> str:
    q = a.get("question") or a.get("q") or ""
    if name == "council":
        return _ask("/council", q)
    if name == "council_deep":
        return _ask("/council-deep", q)
    if name == "council_grounded":
        return _ask("/grounded", q)
    return json.dumps({"error": f"unknown tool: {name}"})


async def on_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOLS)


async def on_call_tool(ctx, params) -> types.CallToolResult:
    out = await asyncio.to_thread(_dispatch, params.name, params.arguments or {})
    return types.CallToolResult(content=[types.TextContent(type="text", text=out)])


server = Server("cyberwarex-council", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def _main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
