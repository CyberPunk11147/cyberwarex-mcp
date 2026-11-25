# CyberWareX MCP Servers

MCP (Model Context Protocol) servers for the [CyberWareX](https://cyberwarex.com) suite —
pay-per-call APIs built for AI agents. Every backing API is **live**, speaks
[x402](https://docs.x402.org) (HTTP 402 → pay USDC on Base → result), and needs
**no account and no API key**: the first paid call is the entire onboarding.

## Hosted: web search + page reader in one URL (no install)

`https://search.cyberwarex.com/mcp` (MCP Streamable HTTP). Tools: `web_search`, `read_page` (HTML or PDF to
markdown), `search_and_read`, `web_search_batch` (10 queries, one payment), `news`, `answer`. 3 free calls a day,
then $0.002-$0.03 a call in USDC on Base via x402, or a prepaid `cwx_` key in the `X-Subscription-Key` header.

Claude Code: `claude mcp add --transport http cyberwarex-search https://search.cyberwarex.com/mcp`

Cursor (`~/.cursor/mcp.json`) and Windsurf (`~/.codeium/windsurf/mcp_config.json`, key `serverUrl`):

```json
{"mcpServers": {"cyberwarex-search": {"url": "https://search.cyberwarex.com/mcp"}}}
```

## Hosted: domain risk verdict + DNS, RDAP, TLS facts (no install)

`https://domain.cyberwarex.com/mcp` (MCP Streamable HTTP). Tools: `domain_score` (risk 0-100, low/medium/high
with plain reasons, from DNS, RDAP age and the TLS certificate), `domain_score_batch` (up to 20 domains, one
payment), `dns_records`, `rdap_lookup`, `ssl_certificate`. $0.002-$0.04 a call in USDC on Base via x402; an unpaid
call returns the invoice in the tool error.

Claude Code: `claude mcp add --transport http cyberwarex-domain https://domain.cyberwarex.com/mcp`

```json
{"mcpServers": {"cyberwarex-domain": {"url": "https://domain.cyberwarex.com/mcp"}}}
```

| Server | Tools | What it does | Price |
|---|---|---|---|
| [`web-access`](web-access/) | `web_fetch` `web_extract` `web_screenshot` `web_pdf` `telegram_search` `telegram_lookup` `telegram_check` `social_profile` `company_profile` | Read any web page as LLM-ready markdown (JavaScript sites too), CSS extraction, screenshots, PDFs. **Telegram search API**: find public Telegram channels, groups and bots for a topic, look up any public @name with its latest posts, check if a channel is alive or inflated, no Telegram account. Public X, TikTok, Instagram, Facebook, LinkedIn profiles and company cards by domain | $0.001–$0.01 |
| [`defi-oracle`](defi-oracle/) | `token_safety` `honeypot_check` `contract_risk` `wallet_identity` `x_identity` `token_identity` `identity_quick` `crowd_check_verdict` `crowd_check_card` `watch_start` `watch_poll` | "Is this token safe to trade?" — live buy/sell simulation, contract powers, A–F grade with evidence (Base + BSC). **New: Crypto Identity Graph** — who is behind a wallet, token or X account (age, ENS, linked X, creator history, impersonation/rug signals from 130k+ graded tokens). **3 free calls/day** with header `X-Free-Trial: 1` — try before funding a wallet | $0.001–$0.06 |
| [`chain-data`](chain-data/) | `chain_wallet` `chain_token` `chain_price` `chain_gas` `chain_tx` `chain_ens` | EVM data across Base, Ethereum, Arbitrum, Optimism, Polygon — no RPC keys, no node | $0.002–$0.004 |
| [`wallet-safety`](wallet-safety/) | `simulate_transaction` `decode_calldata` `decode_signature_request` `address_risk` `url_safety` | Pre-sign safety for wallet/trading agents: simulate a tx before signing, decode EIP-712/personal_sign requests (drainer flags), sanctions + scam-ledger address screening, URL phishing score. 6 chains. **3 free calls/day** per service | $0.002–$0.01 |

Retired: `council` (Agent Council) was shut down on 2026-11-04 and `voice-stt` (voice to text) on 2026-10-18; both are marked deprecated in the MCP registry. Their code stays in the repo for reference; the services no longer answer.

## Recipe: verdict before the swap, watch after the buy

A runnable x402 example for trading agents: [`recipes/swap_guard.py`](recipes/swap_guard.py). One call says go, caution or avoid before the swap ($0.03); after the buy a watch alerts on a pool drain or a price collapse ($0.10 to start, $0.01 a poll). First calls can be tried free with `X-Free-Trial: 1` (3 a day per IP). Not financial advice.

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
    "cyberwarex-chain":  { "command": "python", "args": ["/path/to/cyberwarex-mcp/chain-data/mcp_server.py"] }
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

Defaults point at the live public services — they work out of the box.

## License

MIT — see [LICENSE](LICENSE). Contact: x402@cyberwarex.com
