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
| Vorlage with PDFs | `GET /scrape/vorlage/{kvonr}/with-pdfs` | Vorlage + extracted PDF text |
| PDF Content | `GET /scrape/pdf?url=` | Extract text from a PDF document |
| Session Calendar | `GET /scrape/sessions?year=&month=` | Monthly session overview |
| Search Vorlagen | `GET /scrape/search?query=&page=` | Keyword search for proposals |

### PDF Parsing

The server can extract text from PDF documents (attachments, protocols, etc.):

```bash
# Extract text from a single PDF
curl "http://localhost:8766/scrape/pdf?url=https://ratsinformation.stadt-koeln.de/getfile.asp?..."

# Get a Vorlage with all PDF contents extracted
curl "http://localhost:8766/scrape/vorlage/12345/with-pdfs"
```

**Warning:** PDF parsing is slower (5-30 seconds per document) and memory-intensive. Enable only when needed.

### HTML vs. PDF Content Comparison

The RIS system stores information in two places with very different content:

| Source | Content | Size | Use Case |
|--------|---------|------|----------|
| **HTML Pages** | Metadata only (title, date, committee, status) | ~200 characters | Quick overview, filtering |
| **PDF Documents** | Full content (Begründungen, analyses, attachments) | ~10,000+ characters | Detailed analysis, research |

**Recommendation:** Enable PDF parsing for meaningful queries. HTML pages contain almost no substantive information—all the actual content (reasoning, expert opinions, maps, plans) is in the PDFs.

**Important:** Decision outcomes (beschlossen/abgelehnt/vertagt) are **not** contained in Vorlage PDFs. These are recorded in separate **Beschlussprotokolle** (decision protocols) from the committee meetings. To find out if a proposal was approved, you need to look at the meeting minutes, not the proposal documents.

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

| Provider | Cost | API Key Required | Notes |
|----------|------|------------------|-------|
| Anthropic Claude | Paid | Yes | Best quality, recommended |
| OpenAI | Paid | Yes | GPT-4, GPT-3.5 |
| **Ollama** | **Free** | **No** | Runs locally on your machine |
| Custom | Varies | Yes | Any OpenAI-compatible API |

### Using Ollama (Free Local LLM)

[Ollama](https://ollama.ai) lets you run large language models locally for free.

**1. Install Ollama:**

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.ai/install.sh | sh

# Windows: Download from https://ollama.ai/download
```

**2. Download a model:**

```bash
# Recommended models for tool use:
ollama pull llama3.1        # 8B params, good balance
ollama pull llama3.1:70b    # 70B params, better quality (needs 40GB+ RAM)
ollama pull mistral         # 7B params, fast
ollama pull mixtral         # 8x7B MoE, very capable
ollama pull qwen2.5         # Good for multilingual (German)
ollama pull command-r       # Optimized for tool use
```

**3. Start Ollama:**

```bash
ollama serve
# Runs on http://localhost:11434
```

**4. Configure in Chat UI:**

- Select **"Ollama"** as provider
- Leave API key empty (not required)
- Base URL: `http://localhost:11434/v1` (default)
- Enter model name (e.g., `llama3.1`, `mistral`)

**Recommended Ollama Models:**

| Model | Size | RAM Required | Quality | Speed |
|-------|------|--------------|---------|-------|
| `llama3.1` | 8B | 8GB | Good | Fast |
| `llama3.1:70b` | 70B | 40GB+ | Excellent | Slow |
| `mistral` | 7B | 8GB | Good | Fast |
| `mixtral` | 8x7B | 32GB | Very Good | Medium |
| `qwen2.5` | 7B | 8GB | Good | Fast |
| `command-r` | 35B | 20GB | Very Good (tool use) | Medium |

> **Note:** Smaller models may struggle with complex tool use. For best results with the RIS tools, use `llama3.1:70b`, `mixtral`, or `command-r` if your hardware supports it.

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
