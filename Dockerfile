# Runs the Parlova MCP server over stdio. Listing tools needs no login; to transcribe, pass a token:
#   docker run -i --rm -e PARLOVA_TOKEN=... parlova-mcp
FROM python:3.12-slim
WORKDIR /app
COPY parlova.py .
RUN useradd --create-home parlova
USER parlova
ENTRYPOINT ["python", "parlova.py", "mcp"]
