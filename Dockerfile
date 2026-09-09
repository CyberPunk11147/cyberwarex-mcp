# Root Dockerfile for registry/Glama checks: runs the flagship defi-oracle MCP server (stdio).
# Each subdirectory (web-access, defi-oracle, chain-data, voice-stt) has its own Dockerfile too.
FROM python:3.12-slim
WORKDIR /app
COPY defi-oracle/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY defi-oracle/mcp_server.py .
LABEL io.modelcontextprotocol.server.name="io.github.CyberPunk11147/cyberwarex-defi-oracle"
ENTRYPOINT ["python", "mcp_server.py"]
