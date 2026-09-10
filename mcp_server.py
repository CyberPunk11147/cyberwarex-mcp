"""Root launcher for registry/Glama build specs that run `python mcp_server.py` from the repo root.

Runs the flagship DeFi Safety Oracle MCP server (defi-oracle/mcp_server.py) over stdio.
Set CWX_MCP_SERVER=wallet-safety|chain-data|web-access|voice-stt to launch another one.
No em-dash or en-dash anywhere in this file (hard operator rule): plain hyphens only.
"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
name = os.environ.get("CWX_MCP_SERVER", "defi-oracle").strip() or "defi-oracle"
path = os.path.join(ROOT, name, "mcp_server.py")
if not os.path.isfile(path):
    sys.stderr.write(f"unknown server '{name}'; expected one of: defi-oracle, wallet-safety, chain-data, web-access, voice-stt\n")
    sys.exit(2)
sys.path.insert(0, os.path.join(ROOT, name))
runpy.run_path(path, run_name="__main__")
