"""x402 Agent Search -- MCP server (stdio).

Exposes the Agent Search service (search.cyberwarex.com) as MCP tools so any MCP-speaking agent can
search the web, fetch a page, search+fetch in one call, get a news brief, search papers, batch-search,
or get a single grounded answer -- all keyless, no per-engine API key or scraping on the caller's side.

    search_web(q, category, max_results) -> SERP results JSON
    fetch_page(url, format, max_chars) -> page content as text/markdown
    search_and_fetch(q, max_results, format, max_chars) -> results WITH page content, one call
    news_brief(topic, max_results) -> ranked recent headlines
    search_papers(q, max_results) -> academic/scientific paper search
    search_batch(queries, max_results) -> up to 5 queries in one call
    search_answer(q) -> one grounded answer with sources

It is a thin, stateless proxy to the HTTP service (default https://search.cyberwarex.com, override with
SEARCH_BASE_URL -- set this to the public funnel URL for remote agents).

Payment model: every endpoint is x402-gated. This MCP server forwards an X-PAYMENT header when the
caller provides one via the SEARCH_X_PAYMENT env var, and otherwise surfaces the 402 payment
requirement (the full `accepts` + `extensions.bazaar` block) back to the agent so *its* x402 client
can pay and retry. Payment stays on the calling agent's wallet -- this server never holds keys.

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

BASE_URL = (os.environ.get("SEARCH_BASE_URL") or "https://search.cyberwarex.com").rstrip("/")
X_PAYMENT = os.environ.get("SEARCH_X_PAYMENT", "").strip()
RAPIDAPI_SECRET = os.environ.get("SEARCH_RAPIDAPI_SECRET", "").strip()
HTTP_TIMEOUT = float(os.environ.get("SEARCH_TIMEOUT", "30"))


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
                "header, or set SEARCH_X_PAYMENT for this MCP server.",
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


def _search_web(a: dict) -> str:
    return _get("/search", {"q": a.get("q"), "category": a.get("category", "general"),
                            "max_results": a.get("max_results", 5)})


def _fetch_page(a: dict) -> str:
    return _get("/contents", {"url": a.get("url"), "format": a.get("format", "markdown"),
                              "max_chars": a.get("max_chars", 8000)})


def _search_and_fetch(a: dict) -> str:
    return _get("/search-contents", {"q": a.get("q"), "max_results": a.get("max_results", 3),
                                     "format": a.get("format", "markdown"),
                                     "max_chars": a.get("max_chars", 4000)})


def _news_brief(a: dict) -> str:
    return _get("/news", {"topic": a.get("topic", "general"), "max_results": a.get("max_results", 10)})


def _search_papers(a: dict) -> str:
    return _get("/papers", {"q": a.get("q"), "max_results": a.get("max_results", 10)})


def _search_batch(a: dict) -> str:
    queries = a.get("queries")
    if isinstance(queries, list):
        queries = "|".join(queries)
    return _get("/search-batch", {"queries": queries, "max_results": a.get("max_results", 5)})


def _search_answer(a: dict) -> str:
    return _get("/answer", {"q": a.get("q")})


TOOLS = [
    types.Tool(
        name="search_web",
        description="Search the web and get structured results (title, url, snippet, engine, date). "
                    "category: general|news|images. Up to 10 results. $0.003/call.",
        inputSchema={"type": "object", "required": ["q"], "properties": {
            "q": {"type": "string", "description": "Search query"},
            "category": {"type": "string", "description": "general, news, or images", "default": "general"},
            "max_results": {"type": "integer", "description": "Up to 10", "default": 5},
        }},
    ),
    types.Tool(
        name="fetch_page",
        description="Fetch one web page and return clean readable text/markdown, JS rendered. $0.002/call.",
        inputSchema={"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string", "description": "Page URL to fetch"},
            "format": {"type": "string", "description": "markdown or text", "default": "markdown"},
            "max_chars": {"type": "integer", "description": "Truncate content to this length", "default": 8000},
        }},
    ),
    types.Tool(
        name="search_and_fetch",
        description="Search AND fetch the readable content of each result page in one call -- query to "
                    "grounded sources without a second round trip. Up to 5 pages. $0.006/call.",
        inputSchema={"type": "object", "required": ["q"], "properties": {
            "q": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Up to 5", "default": 3},
            "format": {"type": "string", "description": "markdown or text", "default": "markdown"},
            "max_chars": {"type": "integer", "description": "Per-page content limit", "default": 4000},
        }},
    ),
    types.Tool(
        name="news_brief",
        description="Ranked recent news headlines for a topic, refreshed every 15 min, each with source, "
                    "URL, age and an importance score. Zero-LLM, deterministic. $0.001/call.",
        inputSchema={"type": "object", "properties": {
            "topic": {"type": "string", "description": "Topic or keyword, e.g. crypto", "default": "general"},
            "max_results": {"type": "integer", "default": 10},
        }},
    ),
    types.Tool(
        name="search_papers",
        description="Academic/scientific paper search across arXiv, PubMed, Semantic Scholar, OpenAlex, "
                    "Google Scholar -- title, authors, date, venue, DOI, PDF link. $0.003/call.",
        inputSchema={"type": "object", "required": ["q"], "properties": {
            "q": {"type": "string", "description": "Research query"},
            "max_results": {"type": "integer", "default": 10},
        }},
    ),
    types.Tool(
        name="search_batch",
        description="Run up to 5 web searches in one call, one results block per query -- saves round "
                    "trips for multi-entity research or comparisons. $0.01/call.",
        inputSchema={"type": "object", "required": ["queries"], "properties": {
            "queries": {"description": "Up to 5 queries, as a list or '|'-separated string",
                        "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "string"}]},
            "max_results": {"type": "integer", "description": "Per query", "default": 5},
        }},
    ),
    types.Tool(
        name="search_answer",
        description="One grounded answer to a question, with the sources it was drawn from. $0.03/call.",
        inputSchema={"type": "object", "required": ["q"], "properties": {
            "q": {"type": "string", "description": "Question to answer"},
        }},
    ),
]

_DISPATCH = {
    "search_web": _search_web,
    "fetch_page": _fetch_page,
    "search_and_fetch": _search_and_fetch,
    "news_brief": _news_brief,
    "search_papers": _search_papers,
    "search_batch": _search_batch,
    "search_answer": _search_answer,
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
    "x402-search-api",
    on_list_tools=on_list_tools,
    on_call_tool=on_call_tool,
)


async def _main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
