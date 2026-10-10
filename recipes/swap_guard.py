"""Verdict before the swap, watch after the buy: a runnable x402 example for trading agents (CyberWareX Crowd Check).

Needs: pip install "x402[requests]" eth-account ; a Base wallet holding a little USDC; PRIVATE_KEY in the environment.
Run:   PRIVATE_KEY=0x... python swap_guard.py base 0xTOKEN
Try first, free: send the header X-Free-Trial: 1 (3 calls a day per IP, no wallet needed) with plain requests to see the answers before you wire a wallet.
Cost:  verdict $0.03, then (only if you buy) watch start $0.10, then $0.01 per poll. Nothing is charged for a quote, a computing answer or a bad request.
Not financial advice. A "go" means three questions came back clean, not that the token will rise.
"""
import os
import sys
import time

import requests
from eth_account import Account
from x402 import x402ClientSync
from x402.http.clients import x402_requests
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.client import ExactEvmScheme

BASE = "https://oracle.cyberwarex.com"
NETWORK = "eip155:8453"                      # Base mainnet


def session():
    client = x402ClientSync()
    client.register(NETWORK, ExactEvmScheme(EthAccountSigner(Account.from_key(os.environ["PRIVATE_KEY"]))))
    return x402_requests(client)               # a requests.Session that pays a 402 and retries by itself


def verdict(s, chain, token):
    """One go/no-go answer. 202 means still computing and is never charged: ask again."""
    for _ in range(12):
        r = s.get(f"{BASE}/v1/holders/verdict", params={"chain": chain, "token": token}, timeout=90)
        if r.status_code != 202:
            return r.json()
        time.sleep(10)
    raise SystemExit("still computing, try again in a minute (no charge)")


def main(chain, token):
    s = session()
    v = verdict(s, chain, token)
    print("verdict:", v.get("verdict"), "| score:", v.get("risk_score"), "| reasons:", v.get("reasons"))
    if v.get("verdict") != "ok":
        print("Not buying. (caution and avoid are both a no for an agent that has no human to ask.)")
        return
    # ... your swap goes here, sized small ...
    w = s.get(f"{BASE}/v1/watch/start", params={"chain": chain, "token": token, "hours": 24}, timeout=90).json()
    print("watching, ticket:", w.get("ticket"))
    while True:                                  # every 10 minutes is about $1.44 a day for one token
        time.sleep(600)
        p = s.get(f"{BASE}/v1/watch/poll", params={"ticket": w["ticket"]}, timeout=90)
        if p.status_code == 410:
            print("watch expired"); break
        for a in p.json().get("alerts", []):
            print("ALERT:", a["text"])        # your exit logic goes here


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
