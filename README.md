# CyberWareX MCP Servers

MCP (Model Context Protocol) servers for the [CyberWareX](https://cyberwarex.com) suite —
pay-per-call APIs built for AI agents. Every backing API is **live**, speaks
[x402](https://docs.x402.org) (HTTP 402 → pay USDC on Base → result), and needs
**no account and no API key**: the first paid call is the entire onboarding.

| Server | Tools | What it does | Price |
|---|---|---|---|
| [`web-access`](web-access/) | `web_fetch` `web_extract` `web_screenshot` `web_pdf` | Live web pages as LLM-ready markdown, CSS extraction, screenshots, PDFs | $0.005–$0.01 |
| [`defi-oracle`](defi-oracle/) | `token_safety` `honeypot_check` `contract_risk` | "Is this token safe to trade?" — live buy/sell simulation, contract powers, A–F grade with evidence (Base + BSC) | $0.01–$0.03 |
| [`chain-data`](chain-data/) | `chain_wallet` `chain_token` `chain_price` `chain_gas` `chain_tx` `chain_ens` | EVM data across Base, Ethereum, Arbitrum, Optimism, Polygon — no RPC keys, no node | $0.002–$0.004 |
| [`voice-stt`](voice-stt/) | `voice_transcribe` | Voice messages → text, per 10-second block | $0.015 |

## Install

```bash
pip install mcp requests
```

Each server is a single self-contained file speaking MCP over stdio:

```bash
python web-access/mcp_server.py
```

### Claude Desktop / Claude Code config

```json
{
  "mcpServers": {
    "cyberwarex-web":    { "command": "python", "args": ["/path/to/cyberwarex-mcp/web-access/mcp_server.py"] },
    "cyberwarex-oracle": { "command": "python", "args": ["/path/to/cyberwarex-mcp/defi-oracle/mcp_server.py"] },
    "cyberwarex-chain":  { "command": "python", "args": ["/path/to/cyberwarex-mcp/chain-data/mcp_server.py"] },
    "cyberwarex-voice":  { "command": "python", "args": ["/path/to/cyberwarex-mcp/voice-stt/mcp_server.py"] }
  }
}
```

## How payment works

These servers **never hold keys and never pay**. An unpaid tool call returns the x402
invoice (`x402_payment_required: true` with the full `accepts` block) so the *calling
agent's* x402 client can sign the gasless EIP-3009 USDC authorization and retry —
either through the agent's own payment stack, or by setting the `*_X_PAYMENT` env var
with a pre-signed payment header.

Machine-readable catalog of everything: **https://cyberwarex.com/.well-known/x402**
(LLM-readable summary at [/llms.txt](https://cyberwarex.com/llms.txt)).

## Configuration (env vars)

| Server | Base URL override | Payment header | Timeout |
|---|---|---|---|
| web-access | `AWA_BASE_URL` | `AWA_X_PAYMENT` | `AWA_TIMEOUT` |
| defi-oracle | `DSO_BASE_URL` | `DSO_X_PAYMENT` | `DSO_TIMEOUT` |
| chain-data | `CHAIN_BASE_URL` | `CHAIN_X_PAYMENT` | `CHAIN_TIMEOUT` |
| voice-stt | `VOICE_BASE_URL` | `VOICE_X_PAYMENT` | `VOICE_TIMEOUT` |

Defaults point at the live public services — they work out of the box.

## License

MIT — see [LICENSE](LICENSE). Contact: x402@cyberwarex.com
