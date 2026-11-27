# Token verdict (hosted MCP, no install)

`https://oracle.cyberwarex.com/mcp` (MCP Streamable HTTP). For trading agents: ask before you buy.

- `token_verdict` - simulates a buy and a sell, reads owner powers (mint, pause, blacklist, trading switches) and
  the deployer's past launches; answers allow, review or block with grade A-F, is_honeypot, buy and sell tax and a
  one-line reason. $0.01 a call, $0.005 on a prepaid verdict pack key. A check without a grade is not charged.
- `honeypot_check` - the bare buy and sell test. $0.005.
- `token_safety` - the full A-F evidence report. $0.01.

Chains: base, bsc, ethereum, polygon, arbitrum, robinhood. Pay per call in USDC on Base with x402: an unpaid call
returns the invoice in the tool error; pay it and retry with `x_payment`. Or send header `X-Subscription-Key: cwx_...`.

Claude Code: `claude mcp add --transport http cyberwarex-token-verdict https://oracle.cyberwarex.com/mcp`

```json
{"mcpServers": {"cyberwarex-token-verdict": {"url": "https://oracle.cyberwarex.com/mcp"}}}
```
