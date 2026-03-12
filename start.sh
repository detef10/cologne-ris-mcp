#!/bin/bash
# Start Cologne RIS MCP Server
cd "$(dirname "$0")"
source venv/bin/activate
exec uvicorn app:app --host 127.0.0.1 --port 8766
