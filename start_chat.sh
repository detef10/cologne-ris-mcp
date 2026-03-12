#!/bin/bash
# Start the Cologne RIS Chat server
# Make sure the MCP server is running first: ./start.sh or launchctl start com.cologne-ris-mcp

cd "$(dirname "$0")"
source venv/bin/activate

echo "Starting Cologne RIS Chat server on http://127.0.0.1:8767"
echo "Make sure the MCP server is running on port 8766 first!"
echo ""

uvicorn chat_app:app --host 127.0.0.1 --port 8767
