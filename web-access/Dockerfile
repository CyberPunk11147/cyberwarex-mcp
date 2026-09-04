FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mcp_server.py .
LABEL io.modelcontextprotocol.server.name="io.github.CyberPunk11147/cyberwarex-web-access"
ENTRYPOINT ["python", "mcp_server.py"]
