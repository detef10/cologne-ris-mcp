"""
Tag Database for Cologne RIS Beschlüsse
Hybrid tagging: Rule-based + LLM (Ollama)
"""

import sqlite3
import json
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import httpx

# Database path
DB_PATH = Path(__file__).parent / "beschluesse_tags.db"

# Tag categories and their keywords for rule-based tagging
TAG_RULES = {
    # Themen
    "Verkehr": [
        "verkehr", "radweg", "radverkehr", "fahrrad", "straße", "straßen",
        "ampel", "parkplatz", "parken", "tempo 30", "tempo30", "bus", "bahn",
        "öpnv", "kvb", "mobilität", "fußgänger", "gehweg", "kreuzung",
        "bahnhof", "haltestelle", "carsharing", "e-scooter", "fahrradweg"
    ],
    "Klima": [
        "klima", "klimaschutz", "umwelt", "nachhaltigkeit", "co2", "energie",
        "solar", "photovoltaik", "wärme", "wärmeplanung", "grün", "baum",
        "bäume", "park", "grünfläche", "entsiegelung", "hitze"
    ],
    "Bildung": [
        "schule", "schulen", "gymnasium", "grundschule", "gesamtschule",
        "kita", "kindergarten", "bildung", "schüler", "schulhof", "turnhalle",
        "schulweg", "offene ganztagsschule", "ogs"
    ],
    "Soziales": [
        "sozial", "senioren", "seniorennetzwerk", "jugend", "kinder",
        "familie", "inklusion", "behindert", "pflege", "wohnen", "obdachlos",
        "geflüchtete", "flüchtling", "integration", "armut"
    ],
    "Bauen": [
        "bau", "bauen", "bebauung", "bebauungsplan", "hochbau", "tiefbau",
        "sanierung", "neubau", "gebäude", "grundstück", "architekt",
        "stadtplanung", "rahmenplan", "wohnungsbau"
    ],
    "Kultur": [
        "kultur", "museum", "theater", "kunst", "veranstaltung", "fest",
        "konzert", "bibliothek", "bücherei", "denkmal", "denkmalschutz"
    ],
    "Sport": [
        "sport", "sportplatz", "sportanlage", "schwimmbad", "turnhalle",
        "verein", "fußball", "tennis", "basketball", "leichtathletik"
    ],
    "Finanzen": [
        "haushalt", "finanzen", "budget", "kosten", "förderung", "zuschuss",
        "investition", "etat", "mittel", "bezirksorientierte mittel"
    ],
    "Sicherheit": [
        "sicherheit", "polizei", "ordnung", "ordnungsamt", "lärm",
        "beleuchtung", "vandalismus", "kriminalität"
    ],
    "Digitalisierung": [
        "digital", "internet", "wlan", "smart city", "app", "online"
    ],
}

# Vorlage types
TYPE_RULES = {
    "Bürgereingabe": ["bürgereingabe", "bürgerantrag", "§ 24 go"],
    "Antrag": ["antrag", "an/"],
    "Anfrage": ["anfrage", "anf/"],
    "Mitteilung": ["mitteilung", "kenntnisnahme", "sachstand", "bericht"],
    "Beschlussvorlage": ["beschlussvorlage", "beschluss"],
    "Dringlichkeit": ["dringlichkeit", "dringlichkeitsentscheidung"],
}

# Bezirke mapping
BEZIRKE = {
    20: "Innenstadt",
    21: "Rodenkirchen",
    22: "Lindenthal",
    23: "Ehrenfeld",
    24: "Nippes",
    25: "Chorweiler",
    26: "Porz",
    27: "Kalk",
    28: "Mülheim",
    1: "Rat (Gesamtstadt)",
}

# Reverse lookup for Bezirke names
BEZIRKE_NAMES = {v.lower(): v for v in BEZIRKE.values()}
BEZIRKE_NAMES["rat"] = "Rat (Gesamtstadt)"
BEZIRKE_NAMES["gesamtstadt"] = "Rat (Gesamtstadt)"


def init_database():
    """Initialize the SQLite database with required tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Main table for Beschlüsse
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS beschluesse (
            kvonr INTEGER PRIMARY KEY,
            vorlage_nr TEXT,
            title TEXT NOT NULL,
            gremium_id INTEGER,
            gremium_name TEXT,
            session_id INTEGER,
            session_date TEXT,
            url TEXT,
            indexed_at TEXT,
            tagging_method TEXT  -- 'rule', 'llm', 'hybrid'
        )
    """)

    # Tags table (many-to-many)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            category TEXT  -- 'thema', 'typ', 'bezirk', 'status'
        )
    """)

    # Junction table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS beschluss_tags (
            beschluss_id INTEGER,
            tag_id INTEGER,
            confidence REAL DEFAULT 1.0,  -- 0.0 to 1.0
            PRIMARY KEY (beschluss_id, tag_id),
            FOREIGN KEY (beschluss_id) REFERENCES beschluesse(kvonr),
            FOREIGN KEY (tag_id) REFERENCES tags(id)
        )
    """)

    # Full-text search index
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS beschluesse_fts USING fts5(
            title,
            vorlage_nr,
            content='beschluesse',
            content_rowid='kvonr'
        )
    """)

    # Index for faster queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_date ON beschluesse(session_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_gremium ON beschluesse(gremium_id)")

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


def get_or_create_tag(cursor, tag_name: str, category: str) -> int:
    """Get existing tag ID or create new tag."""
    cursor.execute("SELECT id FROM tags WHERE name = ?", (tag_name,))
    result = cursor.fetchone()
    if result:
        return result[0]

    cursor.execute(
        "INSERT INTO tags (name, category) VALUES (?, ?)",
        (tag_name, category)
    )
    return cursor.lastrowid


def apply_rule_based_tags(title: str) -> List[Tuple[str, str, float]]:
    """
    Apply rule-based tagging to a title.
    Returns list of (tag_name, category, confidence) tuples.
    """
    tags = []
    title_lower = title.lower()

    # Check theme tags
    for tag, keywords in TAG_RULES.items():
        for keyword in keywords:
            if keyword in title_lower:
                tags.append((tag, "thema", 1.0))
                break

    # Check type tags
    for tag, keywords in TYPE_RULES.items():
        for keyword in keywords:
            if keyword in title_lower:
                tags.append((tag, "typ", 1.0))
                break

    return tags


async def apply_llm_tags(title: str, existing_tags: List[str]) -> List[Tuple[str, str, float]]:
    """
    Use Ollama/Qwen to suggest additional tags.
    Only called if rule-based tagging found few or no tags.
    """
    prompt = f"""Analysiere diesen Beschluss-Titel aus dem Kölner Ratsinformationssystem und vergib passende Tags.

Titel: "{title}"

Bereits erkannte Tags: {', '.join(existing_tags) if existing_tags else 'keine'}

Verfügbare Themen-Tags: Verkehr, Klima, Bildung, Soziales, Bauen, Kultur, Sport, Finanzen, Sicherheit, Digitalisierung

Antworte NUR mit einer JSON-Liste der passenden Tags, z.B.: ["Verkehr", "Klima"]
Wenn keine Tags passen, antworte mit: []
Keine Erklärungen, nur das JSON-Array."""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": "qwen2.5:7b",
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1}
                }
            )

            if response.status_code == 200:
                result = response.json().get("response", "[]")
                # Extract JSON array from response
                match = re.search(r'\[.*?\]', result, re.DOTALL)
                if match:
                    suggested_tags = json.loads(match.group())
                    return [(tag, "thema", 0.8) for tag in suggested_tags
                            if tag in TAG_RULES and tag not in existing_tags]
    except Exception as e:
        print(f"LLM tagging failed: {e}")

    return []


async def tag_beschluss_async(
    kvonr: int,
    vorlage_nr: str,
    title: str,
    gremium_id: Optional[int] = None,
    session_id: Optional[int] = None,
    session_date: Optional[str] = None,
    url: Optional[str] = None,
    use_llm: bool = True
) -> Dict:
    """
    Tag a single Beschluss using hybrid approach (async version).
    Returns the tagged Beschluss with all assigned tags.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if already exists
    cursor.execute("SELECT kvonr FROM beschluesse WHERE kvonr = ?", (kvonr,))
    if cursor.fetchone():
        conn.close()
        return {"status": "exists", "kvonr": kvonr}

    # Apply rule-based tags first
    rule_tags = apply_rule_based_tags(title)
    tagging_method = "rule"

    # If few tags found and LLM enabled, try LLM
    llm_tags = []
    if use_llm and len(rule_tags) < 2:
        existing_tag_names = [t[0] for t in rule_tags]
        llm_tags = await apply_llm_tags(title, existing_tag_names)
        if llm_tags:
            tagging_method = "hybrid"

    all_tags = rule_tags + llm_tags

    # Add Bezirk tag if gremium known
    if gremium_id and gremium_id in BEZIRKE:
        all_tags.append((BEZIRKE[gremium_id], "bezirk", 1.0))

    # Insert Beschluss
    gremium_name = BEZIRKE.get(gremium_id, "") if gremium_id else None
    cursor.execute("""
        INSERT INTO beschluesse
        (kvonr, vorlage_nr, title, gremium_id, gremium_name, session_id, session_date, url, indexed_at, tagging_method)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        kvonr, vorlage_nr, title, gremium_id, gremium_name,
        session_id, session_date, url,
        datetime.now().isoformat(), tagging_method
    ))

    # Insert tags
    for tag_name, category, confidence in all_tags:
        tag_id = get_or_create_tag(cursor, tag_name, category)
        cursor.execute("""
            INSERT OR IGNORE INTO beschluss_tags (beschluss_id, tag_id, confidence)
            VALUES (?, ?, ?)
        """, (kvonr, tag_id, confidence))

    # Update FTS index
    cursor.execute("""
        INSERT INTO beschluesse_fts (rowid, title, vorlage_nr)
        VALUES (?, ?, ?)
    """, (kvonr, title, vorlage_nr))

    conn.commit()
    conn.close()

    return {
        "status": "created",
        "kvonr": kvonr,
        "title": title,
        "tags": [t[0] for t in all_tags],
        "method": tagging_method
    }


def tag_beschluss(
    kvonr: int,
    vorlage_nr: str,
    title: str,
    gremium_id: Optional[int] = None,
    session_id: Optional[int] = None,
    session_date: Optional[str] = None,
    url: Optional[str] = None,
    use_llm: bool = False  # Disable LLM by default for sync version
) -> Dict:
    """
    Synchronous wrapper for tag_beschluss_async.
    Uses only rule-based tagging to avoid async issues.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if already exists
    cursor.execute("SELECT kvonr FROM beschluesse WHERE kvonr = ?", (kvonr,))
    if cursor.fetchone():
        conn.close()
        return {"status": "exists", "kvonr": kvonr}

    # Apply rule-based tags only (no async LLM call)
    rule_tags = apply_rule_based_tags(title)
    tagging_method = "rule"
    all_tags = rule_tags

    # Add Bezirk tag if gremium known
    if gremium_id and gremium_id in BEZIRKE:
        all_tags.append((BEZIRKE[gremium_id], "bezirk", 1.0))

    # Insert Beschluss
    gremium_name = BEZIRKE.get(gremium_id, "") if gremium_id else None
    cursor.execute("""
        INSERT INTO beschluesse
        (kvonr, vorlage_nr, title, gremium_id, gremium_name, session_id, session_date, url, indexed_at, tagging_method)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        kvonr, vorlage_nr, title, gremium_id, gremium_name,
        session_id, session_date, url,
        datetime.now().isoformat(), tagging_method
    ))

    # Insert tags
    for tag_name, category, confidence in all_tags:
        tag_id = get_or_create_tag(cursor, tag_name, category)
        cursor.execute("""
            INSERT OR IGNORE INTO beschluss_tags (beschluss_id, tag_id, confidence)
            VALUES (?, ?, ?)
        """, (kvonr, tag_id, confidence))

    # Update FTS index
    cursor.execute("""
        INSERT INTO beschluesse_fts (rowid, title, vorlage_nr)
        VALUES (?, ?, ?)
    """, (kvonr, title, vorlage_nr))

    conn.commit()
    conn.close()

    return {
        "status": "created",
        "kvonr": kvonr,
        "title": title,
        "tags": [t[0] for t in all_tags],
        "method": tagging_method
    }


def parse_smart_query(query: str) -> Dict:
    """
    Parse a natural language query to extract structured search parameters.
    Returns: {
        'bezirk': str or None,
        'typ': str or None,
        'themen': List[str],
        'sort_recent': bool,
        'singular': bool,  # True if user wants exactly one result
        'remaining_text': str
    }
    """
    query_lower = query.lower()
    result = {
        'bezirk': None,
        'typ': None,
        'themen': [],
        'sort_recent': False,
        'singular': False,
        'remaining_text': query
    }

    # Check for singular indicators (user wants exactly ONE result)
    singular_patterns = [
        r'\bder letzte\b', r'\bdie letzte\b', r'\bdas letzte\b',
        r'\bder neueste\b', r'\bdie neueste\b', r'\bdas neueste\b',
        r'\bder aktuellste\b', r'\bdie aktuellste\b', r'\bdas aktuellste\b',
        r'\bletzten\b(?!\s+\d)',  # "letzten" but not "letzten 5"
        r'\bneuste\b', r'\baktuellste\b',
        r'\bgenau ein\b', r'\bein einzige\b',
    ]
    for pattern in singular_patterns:
        if re.search(pattern, query_lower):
            result['singular'] = True
            result['sort_recent'] = True
            break

    # Check for Bezirk mentions
    for name_lower, name in BEZIRKE_NAMES.items():
        if name_lower in query_lower:
            result['bezirk'] = name
            # Remove from remaining text
            pattern = re.compile(re.escape(name_lower), re.IGNORECASE)
            result['remaining_text'] = pattern.sub('', result['remaining_text'])
            break

    # Check for document type mentions
    for typ, keywords in TYPE_RULES.items():
        for kw in keywords:
            if kw in query_lower:
                result['typ'] = typ
                pattern = re.compile(re.escape(kw), re.IGNORECASE)
                result['remaining_text'] = pattern.sub('', result['remaining_text'])
                break
        if result['typ']:
            break

    # Check for topic/theme mentions
    for thema, keywords in TAG_RULES.items():
        for kw in keywords:
            if kw in query_lower and thema not in result['themen']:
                result['themen'].append(thema)
                break

    # Check for "recent" indicators (if not already set by singular detection)
    if not result['sort_recent']:
        recent_words = ['letzt', 'aktuell', 'neu', 'recent', 'heute', 'diese woche', 'dieser monat']
        for word in recent_words:
            if word in query_lower:
                result['sort_recent'] = True
                break

    # Clean up remaining text
    # Remove common filler words
    filler = ['der', 'die', 'das', 'in', 'im', 'zu', 'zum', 'zur', 'von', 'vom',
              'ein', 'eine', 'einer', 'eines', 'titel', 'zeige', 'zeig', 'mir',
              'was', 'ist', 'sind', 'welche', 'welcher', 'letzten', 'letzte',
              'aktuellen', 'neueste', 'neuesten', 'aktuellste', 'aktuellsten']
    words = result['remaining_text'].split()
    words = [w for w in words if w.lower() not in filler and len(w) > 2]
    result['remaining_text'] = ' '.join(words)

    return result


def escape_fts_query(query: str) -> str:
    """Escape special FTS5 characters in search query."""
    # FTS5 special characters that need escaping
    special_chars = ['"', "'", '*', '?', '+', '-', '(', ')', '{', '}', '[', ']', '^', '~', ':', '\\']
    escaped = query
    for char in special_chars:
        escaped = escaped.replace(char, ' ')
    # Remove multiple spaces and trim
    escaped = ' '.join(escaped.split())
    # Wrap each word in quotes for exact matching
    if escaped:
        words = escaped.split()
        escaped = ' OR '.join(f'"{word}"' for word in words if word)
    return escaped


def search_by_tags(
    tags: List[str] = None,
    query: str = None,
    gremium_id: int = None,
    date_from: str = None,
    date_to: str = None,
    limit: int = 50
) -> List[Dict]:
    """
    Fast search in the tag database.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Build query
    if query:
        # Escape special FTS5 characters
        safe_query = escape_fts_query(query)
        if not safe_query:
            # If query becomes empty after escaping, return empty results
            conn.close()
            return []
        # Use FTS for text search
        cursor.execute("""
            SELECT b.*, GROUP_CONCAT(t.name) as tags
            FROM beschluesse b
            LEFT JOIN beschluss_tags bt ON b.kvonr = bt.beschluss_id
            LEFT JOIN tags t ON bt.tag_id = t.id
            WHERE b.kvonr IN (
                SELECT rowid FROM beschluesse_fts WHERE beschluesse_fts MATCH ?
            )
            GROUP BY b.kvonr
            ORDER BY b.session_date DESC
            LIMIT ?
        """, (safe_query, limit))
    elif tags:
        # Search by tags
        placeholders = ','.join('?' * len(tags))
        cursor.execute(f"""
            SELECT b.*, GROUP_CONCAT(DISTINCT t.name) as tags
            FROM beschluesse b
            JOIN beschluss_tags bt ON b.kvonr = bt.beschluss_id
            JOIN tags t ON bt.tag_id = t.id
            WHERE t.name IN ({placeholders})
            {'AND b.gremium_id = ?' if gremium_id else ''}
            {'AND b.session_date >= ?' if date_from else ''}
            {'AND b.session_date <= ?' if date_to else ''}
            GROUP BY b.kvonr
            HAVING COUNT(DISTINCT t.name) >= ?
            ORDER BY b.session_date DESC
            LIMIT ?
        """, (*tags, *([gremium_id] if gremium_id else []),
              *([date_from] if date_from else []),
              *([date_to] if date_to else []),
              min(len(tags), 2), limit))
    else:
        # Return recent
        cursor.execute("""
            SELECT b.*, GROUP_CONCAT(t.name) as tags
            FROM beschluesse b
            LEFT JOIN beschluss_tags bt ON b.kvonr = bt.beschluss_id
            LEFT JOIN tags t ON bt.tag_id = t.id
            {'WHERE b.gremium_id = ?' if gremium_id else ''}
            GROUP BY b.kvonr
            ORDER BY b.session_date DESC
            LIMIT ?
        """.format(), (*([gremium_id] if gremium_id else []), limit))

    results = []
    for row in cursor.fetchall():
        results.append({
            "kvonr": row["kvonr"],
            "vorlage_nr": row["vorlage_nr"],
            "title": row["title"],
            "gremium": row["gremium_name"],
            "date": row["session_date"],
            "url": row["url"],
            "tags": row["tags"].split(",") if row["tags"] else []
        })

    conn.close()
    return results


def smart_search(query: str, limit: int = 20) -> Dict:
    """
    Smart search that parses natural language queries and extracts:
    - Bezirk (district) filters
    - Document type filters
    - Topic/theme filters
    - Recency preferences

    Returns results sorted by relevance and recency.
    """
    parsed = parse_smart_query(query)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Build dynamic query based on parsed parameters
    conditions = []
    params = []

    # Filter by Bezirk
    if parsed['bezirk']:
        conditions.append("b.gremium_name = ?")
        params.append(parsed['bezirk'])

    # Filter by document type (tag)
    if parsed['typ']:
        conditions.append("""
            b.kvonr IN (
                SELECT bt.beschluss_id FROM beschluss_tags bt
                JOIN tags t ON bt.tag_id = t.id
                WHERE t.name = ? AND t.category = 'typ'
            )
        """)
        params.append(parsed['typ'])

    # Filter by themes (tags)
    if parsed['themen']:
        theme_placeholders = ','.join('?' * len(parsed['themen']))
        conditions.append(f"""
            b.kvonr IN (
                SELECT bt.beschluss_id FROM beschluss_tags bt
                JOIN tags t ON bt.tag_id = t.id
                WHERE t.name IN ({theme_placeholders}) AND t.category = 'thema'
            )
        """)
        params.extend(parsed['themen'])

    # Text search on remaining query
    if parsed['remaining_text'].strip():
        safe_query = escape_fts_query(parsed['remaining_text'])
        if safe_query:
            conditions.append("""
                b.kvonr IN (
                    SELECT rowid FROM beschluesse_fts WHERE beschluesse_fts MATCH ?
                )
            """)
            params.append(safe_query)

    # Build WHERE clause
    where_clause = " AND ".join(conditions) if conditions else "1=1"

    # Order by: prefer recent if requested, otherwise by indexed_at
    order_by = "b.session_date DESC NULLS LAST, b.indexed_at DESC"

    # If user asked for singular (e.g., "der letzte"), limit to 1
    effective_limit = 1 if parsed['singular'] else limit

    cursor.execute(f"""
        SELECT b.*, GROUP_CONCAT(DISTINCT t.name) as tags
        FROM beschluesse b
        LEFT JOIN beschluss_tags bt ON b.kvonr = bt.beschluss_id
        LEFT JOIN tags t ON bt.tag_id = t.id
        WHERE {where_clause}
        GROUP BY b.kvonr
        ORDER BY {order_by}
        LIMIT ?
    """, (*params, effective_limit))

    results = []
    for row in cursor.fetchall():
        results.append({
            "kvonr": row["kvonr"],
            "vorlage_nr": row["vorlage_nr"],
            "title": row["title"],
            "gremium": row["gremium_name"],
            "date": row["session_date"],
            "url": row["url"],
            "tags": row["tags"].split(",") if row["tags"] else []
        })

    conn.close()

    return {
        "parsed": {
            "bezirk": parsed['bezirk'],
            "typ": parsed['typ'],
            "themen": parsed['themen'],
            "singular": parsed['singular'],
            "text": parsed['remaining_text']
        },
        "count": len(results),
        "results": results
    }


def get_all_tags() -> Dict[str, List[str]]:
    """Get all tags grouped by category."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT name, category, COUNT(bt.beschluss_id) as count
        FROM tags t
        LEFT JOIN beschluss_tags bt ON t.id = bt.tag_id
        GROUP BY t.id
        ORDER BY count DESC
    """)

    tags_by_category = {}
    for name, category, count in cursor.fetchall():
        if category not in tags_by_category:
            tags_by_category[category] = []
        tags_by_category[category].append({"name": name, "count": count})

    conn.close()
    return tags_by_category


def get_stats() -> Dict:
    """Get database statistics."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM beschluesse")
    total_beschluesse = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM tags")
    total_tags = cursor.fetchone()[0]

    cursor.execute("""
        SELECT tagging_method, COUNT(*)
        FROM beschluesse
        GROUP BY tagging_method
    """)
    by_method = dict(cursor.fetchall())

    cursor.execute("""
        SELECT gremium_name, COUNT(*)
        FROM beschluesse
        WHERE gremium_name IS NOT NULL
        GROUP BY gremium_name
        ORDER BY COUNT(*) DESC
    """)
    by_gremium = dict(cursor.fetchall())

    # Get last indexed timestamp
    cursor.execute("SELECT MAX(indexed_at) FROM beschluesse")
    last_indexed = cursor.fetchone()[0]

    # Get count of LLM-tagged entries
    cursor.execute("SELECT COUNT(*) FROM beschluesse WHERE tagging_method = 'hybrid'")
    llm_tagged = cursor.fetchone()[0]

    conn.close()

    return {
        "total_beschluesse": total_beschluesse,
        "total_tags": total_tags,
        "by_method": by_method,
        "by_gremium": by_gremium,
        "last_indexed": last_indexed,
        "llm_tagged": llm_tagged,
        "database_path": str(DB_PATH)
    }


if __name__ == "__main__":
    # Initialize database when run directly
    init_database()
    print("Database initialized. Stats:", get_stats())
