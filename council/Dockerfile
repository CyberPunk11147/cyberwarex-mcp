FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mcp_server.py .
LABEL io.modelcontextprotocol.server.name="io.github.cyberwareX/cyberwarex-council"
ENTRYPOINT ["python", "mcp_server.py"]
