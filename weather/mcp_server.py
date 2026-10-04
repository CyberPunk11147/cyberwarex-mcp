"""x402 Weather -- MCP server (stdio).

Exposes the Weather service (weather.cyberwarex.com) as MCP tools: current conditions, an
hourly/daily forecast, and air quality -- global, keyless (met.no + open-meteo), by lat+lon or
place name.

    weather_current(place|lat/lon) -> temperature, humidity, wind, pressure, cloud cover, condition
    weather_forecast(place|lat/lon, days, hours) -> current + hourly (<=48h) + daily (<=10d) summary
    weather_air(place|lat/lon) -> AQI (EU + US), PM2.5, PM10, ozone, NO2, SO2, CO, dust, UV index

It is a thin, stateless proxy to the HTTP service (default https://weather.cyberwarex.com, override
with WEATHER_BASE_URL -- set this to the public funnel URL for remote agents).

Payment model: all three endpoints are x402-gated ($0.001-$0.002/call). This MCP server forwards an
X-PAYMENT header when the caller provides one via the WEATHER_X_PAYMENT env var, and otherwise
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

BASE_URL = (os.environ.get("WEATHER_BASE_URL") or "https://weather.cyberwarex.com").rstrip("/")
X_PAYMENT = os.environ.get("WEATHER_X_PAYMENT", "").strip()
HTTP_TIMEOUT = float(os.environ.get("WEATHER_TIMEOUT", "30"))


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
                "header, or set WEATHER_X_PAYMENT for this MCP server.",
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


def _loc_params(a: dict) -> dict:
    return {"place": a.get("place"), "lat": a.get("lat"), "lon": a.get("lon")}


def _current(a: dict) -> str:
    return _get("/current", _loc_params(a))


def _forecast(a: dict) -> str:
    p = _loc_params(a)
    p["days"] = a.get("days", 7)
    p["hours"] = a.get("hours", 24)
    return _get("/forecast", p)


def _air(a: dict) -> str:
    return _get("/air", _loc_params(a))


_LOC_PROPS = {
    "place": {"type": "string", "description": "City/place name (geocoded) if lat/lon not given."},
    "lat": {"type": "number", "description": "Latitude (use with lon instead of place)."},
    "lon": {"type": "number", "description": "Longitude (use with lat instead of place)."},
}

TOOLS = [
    types.Tool(
        name="weather_current",
        description="Current weather for a location (global, keyless): temperature, humidity, wind "
                    "speed/direction, pressure, cloud cover and a condition symbol (met.no). Pass "
                    "lat+lon, or a place name. $0.001/call.",
        inputSchema={"type": "object", "properties": _LOC_PROPS},
    ),
    types.Tool(
        name="weather_forecast",
        description="Weather forecast for a location (global, keyless): current conditions, an "
                    "hourly series (up to 48h) and a daily summary (up to 10 days: min/max temp + "
                    "condition), from met.no. Pass lat+lon, or a place name. $0.002/call.",
        inputSchema={"type": "object", "properties": {
            **_LOC_PROPS,
            "days": {"type": "integer", "minimum": 1, "maximum": 10, "default": 7,
                     "description": "Daily summary days (1-10)."},
            "hours": {"type": "integer", "minimum": 1, "maximum": 48, "default": 24,
                      "description": "Hourly series length (1-48)."},
        }},
    ),
    types.Tool(
        name="weather_air",
        description="Air quality for a location (global, keyless): European + US AQI with bands, "
                    "PM2.5, PM10, ozone, NO2, SO2, CO, dust and UV index (open-meteo/CAMS). Pass "
                    "lat+lon, or a place name. $0.001/call.",
        inputSchema={"type": "object", "properties": _LOC_PROPS},
    ),
]

_DISPATCH = {
    "weather_current": _current,
    "weather_forecast": _forecast,
    "weather_air": _air,
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
    "x402-weather",
    on_list_tools=on_list_tools,
    on_call_tool=on_call_tool,
)


async def _main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
