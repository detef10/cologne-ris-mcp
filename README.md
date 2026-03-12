# Cologne RIS — MCP Server

A **Model Context Protocol (MCP)** server that provides structured access to Cologne's Ratsinformationssystem (council information system) via both the **OParl API** and **HTML scraping** fallback.

Includes an optional **Chat Interface** for natural language queries using Claude or OpenAI-compatible models.

## Features

- Access to Cologne's council information (Ratsinformationssystem)
- OParl API integration with HTML scraping fallback
- Historical data back to 2004
- Natural language chat interface (optional)
- Tag-based search for decisions (Beschlüsse)

## Quick Start

### 1. Setup

```bash
git clone https://github.com/detef10/cologne-ris-mcp.git
cd cologne-ris-mcp
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Start MCP Server

```bash
./start.sh
# Or manually:
uvicorn app:app --host 127.0.0.1 --port 8766
```

Then open:
- **Test UI**: http://localhost:8766
- **MCP endpoint**: http://localhost:8766/mcp
- **OpenAPI docs**: http://localhost:8766/docs

### 3. Start Chat Interface (Optional)

```bash
./start_chat.sh
# Or manually:
uvicorn chat_app:app --host 127.0.0.1 --port 8767
```

Open http://localhost:8767 for the chat UI.

## MCP Tools Available

### OParl API Tools
| Tool | Endpoint | Description |
|------|----------|-------------|
| Discover OParl | `GET /oparl/discover` | Find the working OParl API entry point |
| Get Body | `GET /oparl/body` | Municipality info with links to all entity lists |
| Organizations | `GET /oparl/organizations?page=` | List committees (Gremien) |
| Meetings | `GET /oparl/meetings?page=` | List sessions (Sitzungen) |
| Papers | `GET /oparl/papers?page=` | List proposals (Vorlagen) |
| Persons | `GET /oparl/persons?page=` | List council members |
| Fetch Object | `GET /oparl/object?url=` | Fetch any OParl object by URL |

### HTML Scraper Tools
| Tool | Endpoint | Description |
|------|----------|-------------|
| Vorlage Detail | `GET /scrape/vorlage/{kvonr}` | Scrape a specific proposal by ID |
| Session Calendar | `GET /scrape/sessions?year=&month=` | Monthly session overview |
| Search Vorlagen | `GET /scrape/search?query=&page=` | Keyword search for proposals |

## Connecting an MCP Client

### Claude Desktop / Cursor / VS Code

Add to your MCP config (`~/.claude/settings.json` or similar):

```json
{
  "mcpServers": {
    "cologne-ris": {
      "url": "http://localhost:8766/mcp"
    }
  }
}
```

### Python MCP Client

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("http://localhost:8766/mcp") as (r, w, _):
    async with ClientSession(r, w) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("discover_oparl", {})
        print(result)
```

## Chat Interface

The chat interface (`chat_app.py`) provides a natural language way to query the council information system.

**Supported LLM Providers:**
- **Anthropic Claude** (recommended)
- **OpenAI** / OpenAI-compatible APIs
- **Ollama** (local models)

Enter your API key in the chat UI settings. Keys are not stored on the server.

## Tag Database (Optional)

For faster searches, you can build a local tag database:

```bash
python indexer.py
```

This creates `beschluesse_tags.db` with tagged decisions for quick lookups.

## Architecture

```
┌──────────────────────────────────┐
│  MCP Clients (Claude, Cursor)    │
│  or Web Test Interface           │
└──────────┬───────────────────────┘
           │ JSON-RPC 2.0 / HTTP
┌──────────▼───────────────────────┐
│  FastAPI + FastAPI-MCP           │
│  http://localhost:8766/mcp       │
├──────────────────────────────────┤
│  Tool Layer                      │
│  ┌─────────┐  ┌────────────────┐ │
│  │ OParl   │  │ HTML Scraper   │ │
│  │ Client  │  │ (BS4 fallback) │ │
│  └────┬────┘  └───────┬────────┘ │
└───────┼───────────────┼──────────┘
        │               │
┌───────▼───────────────▼──────────┐
│  ratsinformation.stadt-koeln.de  │
│  (SessionNet)                    │
└──────────────────────────────────┘
```

## Project Structure

```
cologne-ris-mcp/
├── app.py              # Main MCP server (OParl + scraping)
├── chat_app.py         # Chat interface backend
├── chat_ui.html        # Chat web UI
├── tag_database.py     # Tag-based search functionality
├── indexer.py          # Database indexer
├── requirements.txt    # Python dependencies
├── start.sh            # Start MCP server
├── start_chat.sh       # Start chat server
└── Dockerfile          # Docker deployment
```

## License

MIT License
