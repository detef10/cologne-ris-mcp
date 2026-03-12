"""
Cologne RIS Chat Backend
Natural language chat interface for Cologne's council information system.
Supports Anthropic Claude and OpenAI-compatible providers.
Includes fast tag-based search and full RIS search.
"""

import json
import asyncio
import httpx
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import tag database (optional - graceful fallback if not initialized)
try:
    from tag_database import (
        search_by_tags, get_all_tags, get_stats, init_database, DB_PATH,
        tag_beschluss_async, BEZIRKE, smart_search
    )
    TAG_DB_AVAILABLE = DB_PATH.exists()
except ImportError:
    TAG_DB_AVAILABLE = False
    BEZIRKE = {}
    smart_search = None

# --- Configuration ---
MCP_BASE_URL = "http://127.0.0.1:8766"  # Existing MCP server

# --- FastAPI App ---
app = FastAPI(
    title="Cologne RIS Chat",
    description="Natural language chat interface for Cologne's council information system",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Tool Definitions ---
# These map to the existing MCP server endpoints
TOOLS = [
    {
        "name": "search_vorlagen",
        "description": "Search for Vorlagen (proposals/motions) by keyword. Supports historical data back to 2004. Use this for finding proposals about specific topics.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword (e.g., 'Klima', 'Radverkehr', 'Schulen')"},
                "wahlperiode": {"type": "integer", "description": "Electoral term filter: 1=2004-2009, 2=2009-2014, 4=2014-2020, 5=2020-2025, 7=2025-2030. Omit for all terms."}
            },
            "required": ["query"]
        },
        "endpoint": "/scrape/search"
    },
    {
        "name": "get_vorlage",
        "description": "Get detailed information about a specific Vorlage (proposal) by its ID (kvonr). Returns title, status, attachments, and which committees discussed it.",
        "parameters": {
            "type": "object",
            "properties": {
                "kvonr": {"type": "integer", "description": "The Vorlage ID (kvonr number)"}
            },
            "required": ["kvonr"]
        },
        "endpoint": "/scrape/vorlage/{kvonr}"
    },
    {
        "name": "list_gremien",
        "description": "List all Gremien (committees) including Rat, Ausschuesse (committees), Bezirksvertretungen (district councils), and Fraktionen (party groups).",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Filter by category: 'rat', 'bezirk', 'ausschuss', or 'fraktion'. Omit for all."}
            }
        },
        "endpoint": "/scrape/gremien"
    },
    {
        "name": "get_gremium_sessions",
        "description": "Get all sessions (Sitzungen) for a specific committee (Gremium). Use list_gremien first to find the committee ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "kgrnr": {"type": "integer", "description": "Committee ID (kgrnr). Use 1 for Rat, or get IDs from list_gremien."},
                "wahlperiode": {"type": "integer", "description": "Electoral term filter. Omit for current term."},
                "all_periods": {"type": "boolean", "description": "Set true to get sessions from all electoral terms (historical data)"}
            },
            "required": ["kgrnr"]
        },
        "endpoint": "/scrape/gremium/{kgrnr}/sessions"
    },
    {
        "name": "get_session_details",
        "description": "Get detailed information about a specific session including the agenda (Tagesordnung) and all items discussed.",
        "parameters": {
            "type": "object",
            "properties": {
                "ksinr": {"type": "integer", "description": "Session ID (ksinr)"}
            },
            "required": ["ksinr"]
        },
        "endpoint": "/scrape/session/{ksinr}"
    },
    {
        "name": "search_beschluesse",
        "description": "Search for Beschluesse (decisions) by keyword. Can filter by committee, year range, and type. Allows historical searches back to 2004.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword"},
                "gremium": {"type": "integer", "description": "Filter by committee ID (kgrnr). 1=Rat"},
                "year_from": {"type": "integer", "description": "Start year (e.g., 2020)"},
                "year_to": {"type": "integer", "description": "End year (e.g., 2024)"},
                "vorlage_type": {"type": "string", "description": "Filter by type: 'beschlussvorlage', 'antrag', 'anfrage', 'mitteilung'"}
            },
            "required": ["query"]
        },
        "endpoint": "/scrape/beschluesse"
    },
    {
        "name": "get_wahlperioden",
        "description": "Get list of available Wahlperioden (electoral terms) with their date ranges. Useful for understanding which time periods have data.",
        "parameters": {
            "type": "object",
            "properties": {}
        },
        "endpoint": "/scrape/wahlperioden"
    },
    {
        "name": "get_sessions_calendar",
        "description": "Get the session calendar for a specific month. Shows all meetings scheduled.",
        "parameters": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Year (e.g., 2026)"},
                "month": {"type": "integer", "description": "Month (1-12)"}
            },
            "required": ["year", "month"]
        },
        "endpoint": "/scrape/sessions"
    }
]

# System prompt for the LLM
SYSTEM_PROMPT = """Du bist ein Experte für das Kölner Ratsinformationssystem (RIS).

KRITISCH - SPRACHE:
⚠️ ANTWORTE IMMER AUF DEUTSCH. Niemals Chinesisch, Japanisch oder andere Sprachen verwenden!
⚠️ ALWAYS RESPOND IN GERMAN. Never use Chinese, Japanese or other languages!
⚠️ Wenn die Frage auf Englisch ist, antworte auf Englisch. Sonst IMMER Deutsch.

WICHTIG - TOOL-NUTZUNG:
- Du MUSST die bereitgestellten Tools verwenden, um Informationen abzurufen
- Beschreibe NICHT was du tun würdest - RUFE die Tools direkt auf
- Für Bezirksvertretungen: Nutze list_gremien(category="bezirk") um die IDs zu finden
- Für Sitzungen eines Gremiums: Nutze get_gremium_sessions(kgrnr=ID)
- Für Beschlüsse/Vorlagen: Nutze search_beschluesse oder search_vorlagen

BEKANNTE GREMIEN-IDs (Bezirksvertretungen):
- Bezirksvertretung 1 Innenstadt: kgrnr=20
- Bezirksvertretung 2 Rodenkirchen: kgrnr=21
- Bezirksvertretung 3 Lindenthal: kgrnr=22
- Bezirksvertretung 4 Ehrenfeld: kgrnr=23
- Bezirksvertretung 5 Nippes: kgrnr=24
- Bezirksvertretung 6 Chorweiler: kgrnr=25
- Bezirksvertretung 7 Porz: kgrnr=26
- Bezirksvertretung 8 Kalk: kgrnr=27
- Bezirksvertretung 9 Mülheim: kgrnr=28
- Rat: kgrnr=1

WAHLPERIODEN:
- Wahlperiode 7: 2025-2030 (aktuell)
- Wahlperiode 5: 2020-2025
- Wahlperiode 4: 2014-2020

BEISPIEL-WORKFLOW für "Beschlüsse der Bezirksvertretung Lindenthal":
1. get_gremium_sessions(kgrnr=22) → Liste der Sitzungen
2. get_session_details(ksinr=SESSION_ID) → Tagesordnung mit Beschlüssen

ANTWORT-FORMAT:
- Formatiere Ergebnisse als Liste mit Markdown
- Füge Links hinzu: [Titel](URL)
- Sei präzise und direkt
- Wenn du Session-Details abrufst, zeige die Tagesordnungspunkte

ERINNERUNG: Deine Antwort MUSS auf Deutsch sein (oder Englisch wenn die Frage auf Englisch war)."""


# --- Request/Response Models ---
class Message(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: List[Message] = []
    provider: str = "anthropic"  # "anthropic", "openai", "ollama", "custom"
    model: str = "claude-sonnet-4-20250514"
    api_key: Optional[str] = None  # Optional for Ollama
    base_url: Optional[str] = None  # For custom OpenAI-compatible endpoints


class ChatResponse(BaseModel):
    response: str
    sources: List[Dict[str, str]] = []  # [{title, url}]
    tool_calls: List[str] = []  # Names of tools called


# --- Provider Adapters ---
class LLMProvider:
    """Base class for LLM providers"""

    async def chat_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        api_key: str,
        model: str,
        base_url: Optional[str] = None
    ) -> Dict[str, Any]:
        raise NotImplementedError


class AnthropicProvider(LLMProvider):
    """Anthropic Claude with native tool use"""

    def _convert_tools(self, tools: List[Dict]) -> List[Dict]:
        """Convert our tool format to Anthropic's format"""
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"]
            }
            for t in tools
        ]

    async def chat_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        api_key: str,
        model: str,
        base_url: Optional[str] = None
    ) -> Dict[str, Any]:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        anthropic_tools = self._convert_tools(tools)

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=anthropic_tools,
            messages=messages
        )

        return {
            "content": response.content,
            "stop_reason": response.stop_reason
        }


class OpenAIProvider(LLMProvider):
    """OpenAI with function calling (also works for compatible APIs)"""

    def _convert_tools(self, tools: List[Dict]) -> List[Dict]:
        """Convert our tool format to OpenAI's function calling format"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"]
                }
            }
            for t in tools
        ]

    async def chat_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        api_key: str,
        model: str,
        base_url: Optional[str] = None
    ) -> Dict[str, Any]:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url)
        openai_tools = self._convert_tools(tools)

        # Prepend system message
        full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

        response = client.chat.completions.create(
            model=model,
            messages=full_messages,
            tools=openai_tools,
            tool_choice="auto"
        )

        return {
            "message": response.choices[0].message,
            "finish_reason": response.choices[0].finish_reason
        }


def get_provider(provider_name: str) -> LLMProvider:
    """Factory function to get the appropriate provider"""
    providers = {
        "anthropic": AnthropicProvider(),
        "openai": OpenAIProvider(),
        "ollama": OpenAIProvider(),  # Ollama uses OpenAI-compatible API
        "custom": OpenAIProvider(),  # Custom uses OpenAI-compatible API
    }
    return providers.get(provider_name, AnthropicProvider())


# --- Tool Execution ---
async def execute_tool(tool_name: str, arguments: Dict) -> Dict:
    """Execute a tool by calling the MCP server endpoint"""
    tool = next((t for t in TOOLS if t["name"] == tool_name), None)
    if not tool:
        return {"error": f"Unknown tool: {tool_name}"}

    endpoint = tool["endpoint"]

    # Handle path parameters (e.g., {kvonr})
    for key, value in arguments.items():
        if f"{{{key}}}" in endpoint:
            endpoint = endpoint.replace(f"{{{key}}}", str(value))
            arguments = {k: v for k, v in arguments.items() if k != key}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{MCP_BASE_URL}{endpoint}", params=arguments)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": str(e)}


def extract_sources(tool_results: List[Dict]) -> List[Dict[str, str]]:
    """Extract source URLs from tool results"""
    sources = []
    seen_urls = set()

    for result in tool_results:
        # Handle list results (e.g., from search)
        if "results" in result:
            for item in result["results"][:5]:  # Limit to 5 sources per result
                if "url" in item:
                    url = item["url"]
                    if url not in seen_urls:
                        title = item.get("title", item.get("vorlage_nr", "Source"))
                        sources.append({"title": title, "url": url})
                        seen_urls.add(url)

        # Handle single item results
        if "url" in result and result["url"] not in seen_urls:
            title = result.get("title", result.get("gremium", "Source"))
            sources.append({"title": title, "url": result["url"]})
            seen_urls.add(result["url"])

        # Handle session results
        if "sessions" in result:
            for session in result["sessions"][:3]:
                if "url" in session and session["url"] not in seen_urls:
                    title = session.get("title", session.get("date", "Session"))
                    sources.append({"title": title, "url": session["url"]})
                    seen_urls.add(session["url"])

        # Handle agenda items (Beschlüsse)
        if "agenda_items" in result:
            for item in result["agenda_items"][:5]:
                if "url" in item and item["url"] not in seen_urls:
                    title = item.get("title", item.get("vorlage_nr", "Vorlage"))
                    sources.append({"title": title, "url": item["url"]})
                    seen_urls.add(item["url"])

    return sources[:10]  # Limit total sources


# --- Chat Endpoint ---
@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Process a chat message with tool use"""

    provider = get_provider(request.provider)
    tool_calls_made = []
    tool_results = []

    # Build messages list
    messages = [{"role": m.role, "content": m.content} for m in request.history]
    messages.append({"role": "user", "content": request.message})

    # Filter tools to only include name, description, parameters for the provider
    tools_for_provider = [
        {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
        for t in TOOLS
    ]

    # For Ollama, set default base_url and a dummy API key
    api_key = request.api_key
    base_url = request.base_url
    if request.provider == "ollama":
        base_url = base_url or "http://localhost:11434/v1"
        api_key = api_key or "ollama"  # Ollama doesn't require auth

    try:
        if request.provider == "anthropic":
            # Anthropic flow with tool use loop
            max_iterations = 5

            for _ in range(max_iterations):
                response = await provider.chat_with_tools(
                    messages=messages,
                    tools=tools_for_provider,
                    api_key=api_key,
                    model=request.model,
                    base_url=base_url
                )

                # Check if we need to process tool calls
                if response["stop_reason"] == "tool_use":
                    # Process each content block
                    tool_use_blocks = []
                    text_blocks = []

                    for block in response["content"]:
                        if block.type == "tool_use":
                            tool_name = block.name
                            tool_input = block.input
                            tool_id = block.id

                            # Execute the tool
                            result = await execute_tool(tool_name, tool_input)
                            tool_results.append(result)
                            tool_calls_made.append(tool_name)

                            tool_use_blocks.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": json.dumps(result, ensure_ascii=False)
                            })
                        elif block.type == "text":
                            text_blocks.append(block.text)

                    # Add assistant's response and tool results to messages
                    messages.append({
                        "role": "assistant",
                        "content": response["content"]
                    })
                    messages.append({
                        "role": "user",
                        "content": tool_use_blocks
                    })
                else:
                    # No more tool calls, extract final response
                    final_text = ""
                    for block in response["content"]:
                        if block.type == "text":
                            final_text += block.text

                    return ChatResponse(
                        response=final_text,
                        sources=extract_sources(tool_results),
                        tool_calls=tool_calls_made
                    )

            # Max iterations reached
            return ChatResponse(
                response="I've reached the maximum number of tool calls. Please try a more specific question.",
                sources=extract_sources(tool_results),
                tool_calls=tool_calls_made
            )

        else:
            # OpenAI flow with function calling
            max_iterations = 5

            for _ in range(max_iterations):
                response = await provider.chat_with_tools(
                    messages=messages,
                    tools=tools_for_provider,
                    api_key=api_key,
                    model=request.model,
                    base_url=base_url
                )

                message = response["message"]

                if message.tool_calls:
                    # Add assistant message with tool calls
                    messages.append({
                        "role": "assistant",
                        "content": message.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments
                                }
                            }
                            for tc in message.tool_calls
                        ]
                    })

                    # Execute each tool call
                    for tool_call in message.tool_calls:
                        tool_name = tool_call.function.name
                        tool_args = json.loads(tool_call.function.arguments)

                        result = await execute_tool(tool_name, tool_args)
                        tool_results.append(result)
                        tool_calls_made.append(tool_name)

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(result, ensure_ascii=False)
                        })
                else:
                    # No tool calls, return final response
                    final_content = message.content or ""

                    # If content is empty but we have tool results, generate a summary
                    if not final_content and tool_results:
                        final_content = "Basierend auf den abgerufenen Daten:\n\n"
                        for result in tool_results:
                            if "sessions" in result:
                                final_content += f"**{result.get('gremium', 'Sitzungen')}** ({result.get('count', 0)} Sitzungen):\n"
                                for s in result.get("sessions", [])[:5]:
                                    final_content += f"- [{s.get('title', 'Sitzung')}]({s.get('url', '')})\n"
                            if "agenda_items" in result:
                                final_content += f"\n**Tagesordnung** ({result.get('agenda_count', 0)} Punkte):\n"
                                for item in result.get("agenda_items", [])[:10]:
                                    final_content += f"- [{item.get('title', 'TOP')}]({item.get('url', '')})\n"

                    return ChatResponse(
                        response=final_content,
                        sources=extract_sources(tool_results),
                        tool_calls=tool_calls_made
                    )

            # Max iterations reached - provide summary of what was found
            final_content = "Die maximale Anzahl an Tool-Aufrufen wurde erreicht. Hier sind die gefundenen Informationen:\n\n"
            for result in tool_results:
                if "sessions" in result:
                    final_content += f"**{result.get('gremium', 'Sitzungen')}**:\n"
                    for s in result.get("sessions", [])[:5]:
                        final_content += f"- [{s.get('title', 'Sitzung')}]({s.get('url', '')})\n"
                if "agenda_items" in result:
                    final_content += f"\n**Tagesordnung** ({result.get('agenda_count', 0)} Punkte):\n"
                    for item in result.get("agenda_items", [])[:10]:
                        final_content += f"- [{item.get('title', 'TOP')}]({item.get('url', '')})\n"

            return ChatResponse(
                response=final_content,
                sources=extract_sources(tool_results),
                tool_calls=tool_calls_made
            )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Health Check ---
@app.get("/health")
async def health():
    """Check if the chat server and MCP server are running"""
    mcp_status = "unknown"
    ollama_status = "unknown"
    ollama_models = []

    async with httpx.AsyncClient(timeout=5) as client:
        # Check MCP server
        try:
            response = await client.get(f"{MCP_BASE_URL}/scrape/wahlperioden")
            mcp_status = "ok" if response.status_code == 200 else "error"
        except Exception:
            mcp_status = "unreachable"

        # Check Ollama
        try:
            response = await client.get("http://localhost:11434/api/tags")
            if response.status_code == 200:
                ollama_status = "ok"
                data = response.json()
                ollama_models = [m["name"] for m in data.get("models", [])]
            else:
                ollama_status = "error"
        except Exception:
            ollama_status = "not_running"

    return {
        "chat_server": "ok",
        "mcp_server": mcp_status,
        "mcp_url": MCP_BASE_URL,
        "ollama": ollama_status,
        "ollama_models": ollama_models,
        "tag_database": "available" if TAG_DB_AVAILABLE else "not_initialized"
    }


# --- Fast Search (Tag Database) ---
class FastSearchRequest(BaseModel):
    query: Optional[str] = None
    tags: Optional[List[str]] = None
    gremium_id: Optional[int] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    limit: int = 50


@app.post("/search/fast")
async def fast_search(request: FastSearchRequest):
    """
    Fast search using the local tag database.
    Much faster than RIS search, but only includes indexed Beschlüsse.

    If a natural language query is provided, uses smart_search to extract:
    - Bezirk (district) from query
    - Document type from query
    - Topic tags from query
    - Then filters accordingly
    """
    if not TAG_DB_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Tag database not initialized. Run: python indexer.py --init && python indexer.py --all"
        )

    # Use smart_search for natural language queries (when no explicit tags provided)
    if request.query and not request.tags and smart_search:
        result = smart_search(request.query, limit=request.limit)
        return {
            "source": "smart_search",
            "parsed": result["parsed"],
            "count": result["count"],
            "results": result["results"]
        }

    # Fall back to simple tag-based search
    results = search_by_tags(
        tags=request.tags,
        query=request.query,
        gremium_id=request.gremium_id,
        date_from=request.date_from,
        date_to=request.date_to,
        limit=request.limit
    )

    return {
        "source": "tag_database",
        "count": len(results),
        "results": results
    }


@app.get("/search/tags")
async def list_tags():
    """Get all available tags grouped by category."""
    if not TAG_DB_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Tag database not initialized. Run: python indexer.py --init"
        )

    return get_all_tags()


@app.get("/search/stats")
async def tag_stats():
    """Get tag database statistics."""
    if not TAG_DB_AVAILABLE:
        return {
            "status": "not_initialized",
            "message": "Run: python indexer.py --init && python indexer.py --all"
        }

    return get_stats()


# --- Indexing ---
indexing_status = {"running": False, "progress": "", "last_result": None}


async def fetch_gremium_sessions(kgrnr: int, wahlperiode: int = 7) -> List[Dict]:
    """Fetch sessions for a committee."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{MCP_BASE_URL}/scrape/gremium/{kgrnr}/sessions",
            params={"wahlperiode": wahlperiode}
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("sessions", [])
    return []


async def fetch_session_details(ksinr: int) -> Dict:
    """Fetch session details including agenda items."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{MCP_BASE_URL}/scrape/session/{ksinr}")
        if response.status_code == 200:
            return response.json()
    return {}


async def run_indexer(use_llm: bool = True, max_sessions: int = 3):
    """Background task to index new Beschlüsse."""
    global indexing_status
    indexing_status["running"] = True
    indexing_status["progress"] = "Starting..."

    stats = {"new": 0, "existing": 0, "errors": 0}
    gremien = [22, 20, 21, 23, 24, 25, 26, 27, 28, 1]  # All Bezirksvertretungen + Rat

    try:
        for kgrnr in gremien:
            gremium_name = BEZIRKE.get(kgrnr, f"Gremium {kgrnr}")
            indexing_status["progress"] = f"Indexing {gremium_name}..."

            sessions = await fetch_gremium_sessions(kgrnr, 7)

            for session in sessions[:max_sessions]:
                ksinr = session.get("ksinr")
                session_date = session.get("date")

                details = await fetch_session_details(ksinr)
                agenda_items = details.get("agenda_items", [])

                for item in agenda_items:
                    kvonr = item.get("kvonr")
                    title = item.get("title", "")
                    vorlage_nr = item.get("vorlage_nr", "")
                    url = item.get("url", "")

                    if not kvonr or not title:
                        continue

                    try:
                        result = await tag_beschluss_async(
                            kvonr=kvonr,
                            vorlage_nr=vorlage_nr,
                            title=title,
                            gremium_id=kgrnr,
                            session_id=ksinr,
                            session_date=session_date,
                            url=url,
                            use_llm=use_llm
                        )

                        if result["status"] == "created":
                            stats["new"] += 1
                        else:
                            stats["existing"] += 1
                    except Exception as e:
                        stats["errors"] += 1

                await asyncio.sleep(0.3)  # Be nice to the server

        indexing_status["progress"] = "Done!"
        indexing_status["last_result"] = stats

    except Exception as e:
        import traceback
        error_msg = f"{type(e).__name__}: {str(e)}"
        error_trace = traceback.format_exc()
        indexing_status["progress"] = f"Error: {error_msg}"
        indexing_status["last_result"] = {"error": error_msg, "traceback": error_trace}
        print(f"Indexer error: {error_trace}")

    finally:
        indexing_status["running"] = False


@app.post("/search/index")
async def trigger_indexing(background_tasks: BackgroundTasks, use_llm: bool = True, max_sessions: int = 3):
    """Trigger background indexing of new Beschlüsse."""
    if not TAG_DB_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Tag database not initialized. Run: python indexer.py --init"
        )

    if indexing_status["running"]:
        return {
            "status": "already_running",
            "progress": indexing_status["progress"]
        }

    background_tasks.add_task(run_indexer, use_llm, max_sessions)

    return {
        "status": "started",
        "message": f"Indexing started (LLM: {use_llm}, max_sessions: {max_sessions})"
    }


@app.get("/search/index/status")
async def get_indexing_status():
    """Get current indexing status."""
    return {
        "running": indexing_status["running"],
        "progress": indexing_status["progress"],
        "last_result": indexing_status["last_result"]
    }


# --- Chat UI ---
@app.get("/", response_class=HTMLResponse)
async def chat_ui():
    """Serve the chat UI"""
    with open("chat_ui.html", "r") as f:
        return f.read()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8767)
