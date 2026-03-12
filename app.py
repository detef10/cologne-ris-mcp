import httpx
import json
import re
import time
from typing import Optional, List
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi_mcp import FastApiMCP
from bs4 import BeautifulSoup

# --- Configuration ---
BASE_URL = "https://ratsinformation.stadt-koeln.de"
OPARL_CANDIDATES = [
    f"{BASE_URL}/webservice/oparl/v1.1/system",
    f"{BASE_URL}/webservice/oparl/v1.0/system",
    f"{BASE_URL}/webservice/oparl/v1/system",
]
HTTP_HEADERS = {
    "User-Agent": "CologneRIS-MCP/1.0 (research project)",
    "Accept": "application/json",
}
REQUEST_DELAY = 1.5  # seconds between requests

# Wahlperioden (electoral terms) mapping
WAHLPERIODEN = {
    1: {"name": "2004-2009", "start": "2004-09-26", "end": "2009-10-20"},
    2: {"name": "2009-2014", "start": "2009-10-21", "end": "2014-05-31"},
    4: {"name": "2014-2020", "start": "2014-06-01", "end": "2020-10-31"},
    5: {"name": "2020-2025", "start": "2020-11-01", "end": "2025-10-31"},
    7: {"name": "2025-2030", "start": "2025-11-01", "end": "2030-10-31"},
}

# Well-known Gremien (committees)
GREMIEN = {
    1: "Rat",
    10: "Hauptausschuss",
    11: "Finanzausschuss",
    12: "Liegenschaftsausschuss",
    14: "Ausschuss Allgemeine Verwaltung und Rechtsfragen",
    18: "Ausschuss Klima, Umwelt und Gruen",
    # Add more as needed
}

client = httpx.Client(headers=HTTP_HEADERS, timeout=30, follow_redirects=True)

# --- FastAPI App ---
app = FastAPI(
    title="Cologne RIS MCP Server",
    description="MCP server providing tools to access Cologne's Ratsinformationssystem (council information system) via OParl API and HTML scraping. Supports historical data back to 2004.",
    version="2.0.0",
)

# --- Helper Functions ---
def _oparl_get(url: str) -> dict:
    """Fetch a URL from the OParl API and return JSON."""
    r = client.get(url)
    r.raise_for_status()
    return r.json()

def _oparl_get_page(url: str, page: int = 1) -> dict:
    """Fetch a paginated OParl list."""
    separator = "&" if "?" in url else "?"
    r = client.get(f"{url}{separator}page={page}")
    r.raise_for_status()
    return r.json()

def _html_get(path: str) -> BeautifulSoup:
    """Fetch an HTML page from the RIS and return a BeautifulSoup object."""
    r = client.get(f"{BASE_URL}/{path}", headers={"Accept": "text/html"})
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


# ============================================================
# TOOL 1: Discover OParl API
# ============================================================
@app.get("/oparl/discover", tags=["OParl"], summary="Discover the OParl API entry point",
         description="Probes known OParl endpoint URLs for Cologne's RIS and returns the system object if found.")
def discover_oparl():
    """Try all known OParl endpoint candidates and return the system object."""
    for url in OPARL_CANDIDATES:
        try:
            r = client.get(url)
            if r.status_code == 200:
                data = r.json()
                return {"status": "found", "endpoint": url, "system": data}
        except Exception as e:
            continue
    return {"status": "not_found", "tried": OPARL_CANDIDATES,
            "message": "OParl API not reachable. Use HTML scraping tools instead."}


# ============================================================
# TOOL 2: Get Body (Municipality info)
# ============================================================
@app.get("/oparl/body", tags=["OParl"], summary="Get OParl Body (municipality info)",
         description="Returns the OParl Body object for Cologne, which contains links to all main entity lists.")
def get_oparl_body():
    """Fetch the OParl Body for Cologne."""
    for url in OPARL_CANDIDATES:
        try:
            system = _oparl_get(url)
            body_url = system.get("body")
            if body_url:
                return _oparl_get(body_url)
        except Exception:
            continue
    return {"error": "Could not retrieve OParl body"}


# ============================================================
# TOOL 3: List Organizations (Gremien / Committees)
# ============================================================
@app.get("/oparl/organizations", tags=["OParl"], summary="List organizations (committees)",
         description="Returns a paginated list of Gremien (committees) such as Rat, Ausschuesse, Bezirksvertretungen.")
def list_organizations(page: int = Query(1, ge=1, description="Page number")):
    """List all organizations/committees from the OParl API."""
    try:
        body = get_oparl_body()
        org_url = body.get("organization") or body.get("data", [{}])[0].get("organization")
        if org_url:
            return _oparl_get_page(org_url, page)
    except Exception as e:
        return {"error": str(e)}
    return {"error": "Organization list URL not found in body"}


# ============================================================
# TOOL 4: List Meetings (Sitzungen)
# ============================================================
@app.get("/oparl/meetings", tags=["OParl"], summary="List meetings (Sitzungen)",
         description="Returns a paginated list of council meetings with date, location, and agenda links.")
def list_meetings(page: int = Query(1, ge=1, description="Page number")):
    """List meetings from the OParl API."""
    try:
        body = get_oparl_body()
        meeting_url = body.get("meeting") or body.get("data", [{}])[0].get("meeting")
        if meeting_url:
            return _oparl_get_page(meeting_url, page)
    except Exception as e:
        return {"error": str(e)}
    return {"error": "Meeting list URL not found in body"}


# ============================================================
# TOOL 5: List Papers (Vorlagen / Proposals)
# ============================================================
@app.get("/oparl/papers", tags=["OParl"], summary="List papers (Vorlagen)",
         description="Returns a paginated list of Vorlagen (proposals/motions) with subject and document links.")
def list_papers(page: int = Query(1, ge=1, description="Page number")):
    """List papers/proposals from the OParl API."""
    try:
        body = get_oparl_body()
        paper_url = body.get("paper") or body.get("data", [{}])[0].get("paper")
        if paper_url:
            return _oparl_get_page(paper_url, page)
    except Exception as e:
        return {"error": str(e)}
    return {"error": "Paper list URL not found in body"}


# ============================================================
# TOOL 6: List Persons (Council Members)
# ============================================================
@app.get("/oparl/persons", tags=["OParl"], summary="List persons (council members)",
         description="Returns a paginated list of council members with name, party, and membership links.")
def list_persons(page: int = Query(1, ge=1, description="Page number")):
    """List persons from the OParl API."""
    try:
        body = get_oparl_body()
        person_url = body.get("person") or body.get("data", [{}])[0].get("person")
        if person_url:
            return _oparl_get_page(person_url, page)
    except Exception as e:
        return {"error": str(e)}
    return {"error": "Person list URL not found in body"}


# ============================================================
# TOOL 7: Get OParl Object by URL
# ============================================================
@app.get("/oparl/object", tags=["OParl"], summary="Fetch any OParl object by URL",
         description="Fetches and returns any OParl object given its full URL. Use for drilling into specific meetings, papers, persons, etc.")
def get_oparl_object(url: str = Query(..., description="Full OParl object URL")):
    """Generic OParl object fetcher."""
    try:
        return _oparl_get(url)
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# TOOL 8: HTML Scraper - Vorlage Detail (Enhanced)
# ============================================================
@app.get("/scrape/vorlage/{kvonr}", tags=["HTML Scraper"], summary="Scrape a Vorlage (proposal) by ID",
         description="Scrapes the HTML detail page for a specific Vorlage and extracts structured data including the Beratungsfolge (which Gremien discussed it).")
def scrape_vorlage(kvonr: int):
    """Scrape a single Vorlage detail page with full metadata."""
    try:
        # Get main info page
        soup = _html_get(f"vo0050.asp?__kvonr={kvonr}")
        title_el = soup.find("h1") or soup.find("title")
        title = title_el.get_text(strip=True) if title_el else "Unknown"

        # Extract structured fields from the content table
        details = {}
        for row in soup.select(".smc-table-row"):
            cells = row.select(".smc-table-cell")
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True).rstrip(":")
                val = cells[1].get_text(strip=True)
                if key and val:
                    details[key] = val

        # Extract attachment links
        attachments = []
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "getfile" in href.lower() or href.endswith(".pdf"):
                name = link.get_text(strip=True)
                if name:  # Only add if has a name
                    attachments.append({
                        "name": name,
                        "url": f"{BASE_URL}/{href}" if not href.startswith("http") else href
                    })

        # Get Beratungsfolge (deliberation sequence) from the Beratungen tab
        beratungen = []
        try:
            soup_beratungen = _html_get(f"vo0053.asp?__kvonr={kvonr}")
            # Look for Gremium entries
            for el in soup_beratungen.select(".smc-table-row, tr"):
                text = el.get_text(" ", strip=True)
                # Pattern: Gremium name followed by date and status
                if any(x in text for x in ["oeffentlich", "Entscheidung", "Vorberatung", "Kenntnisnahme"]):
                    # Extract links to sessions
                    session_link = el.find("a", href=lambda h: h and "si0057" in h)
                    gremium_match = re.search(r"(Rat|Ausschuss[^0-9]*|Bezirksvertretung[^0-9]*)", text)
                    date_match = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)

                    beratung = {
                        "gremium": gremium_match.group(1).strip() if gremium_match else None,
                        "date": date_match.group(1) if date_match else None,
                        "status": "Entscheidung" if "Entscheidung" in text else
                                  "Vorberatung" if "Vorberatung" in text else
                                  "Kenntnisnahme" if "Kenntnisnahme" in text else None,
                        "public": "oeffentlich" in text.lower(),
                        "session_url": f"{BASE_URL}/{session_link['href']}" if session_link else None
                    }
                    if beratung["gremium"]:
                        beratungen.append(beratung)
        except Exception:
            pass  # Beratungen page might not exist

        return {
            "kvonr": kvonr,
            "title": title,
            "details": details,
            "beratungen": beratungen,
            "attachments": attachments,
            "url": f"{BASE_URL}/vo0050.asp?__kvonr={kvonr}"
        }
    except Exception as e:
        return {"error": str(e), "kvonr": kvonr}


# ============================================================
# TOOL 9: HTML Scraper - Session Calendar
# ============================================================
@app.get("/scrape/sessions", tags=["HTML Scraper"], summary="Scrape session calendar",
         description="Scrapes the monthly session calendar page and returns a list of upcoming meetings.")
def scrape_sessions(year: int = Query(2026, description="Year"), month: int = Query(3, ge=1, le=12, description="Month")):
    """Scrape the session calendar for a given month."""
    try:
        soup = _html_get(f"si0040.asp?__cjahr={year}&__cmonat={month}")
        sessions = []
        for row in soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) >= 3:
                link = row.find("a", href=True)
                sessions.append({
                    "date": cells[0].get_text(strip=True),
                    "time": cells[1].get_text(strip=True) if len(cells) > 1 else "",
                    "committee": cells[2].get_text(strip=True) if len(cells) > 2 else "",
                    "url": f"{BASE_URL}/{link['href']}" if link else None
                })
        return {"year": year, "month": month, "sessions": sessions}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# TOOL 10: HTML Scraper - Search Vorlagen (Enhanced)
# ============================================================
@app.get("/scrape/search", tags=["HTML Scraper"], summary="Search Vorlagen by keyword",
         description="Searches the RIS for Vorlagen matching a keyword. Supports filtering by Wahlperiode (electoral term) for historical searches back to 2004.")
def search_vorlagen(
    query: str = Query(..., description="Search keyword"),
    page: int = Query(1, ge=1, description="Results page"),
    wahlperiode: Optional[int] = Query(None, description="Electoral term: 1=2004-2009, 2=2009-2014, 4=2014-2020, 5=2020-2025, 7=2025-2030. Leave empty for all.")
):
    """Search for Vorlagen by keyword via the RIS search page."""
    try:
        params = {
            "__ctext": query,
            "__cseite": page,
        }
        if wahlperiode:
            params["__cwpnr"] = wahlperiode

        r = client.get(f"{BASE_URL}/suchen01.asp", params=params)
        soup = BeautifulSoup(r.text, "html.parser")

        results = []
        for row in soup.select("tr, .smc-table-row"):
            link = row.find("a", href=lambda h: h and "vo0050" in h)
            if link:
                # Extract kvonr from URL
                kvonr_match = re.search(r"kvonr=(\d+)", link.get("href", ""))
                kvonr = int(kvonr_match.group(1)) if kvonr_match else None

                # Get all text content
                cells = row.find_all("td") or row.select(".smc-table-cell")
                extra_text = " ".join(c.get_text(strip=True) for c in cells)

                # Try to extract Vorlage number (e.g., "1234/2023")
                vorlage_nr_match = re.search(r"(\d+/\d{4})", extra_text)

                # Try to extract type (Beschlussvorlage, Mitteilung, etc.)
                vorlage_type = None
                for vtype in ["Beschlussvorlage Rat", "Beschlussvorlage", "Mitteilung", "Anfrage", "Antrag"]:
                    if vtype in extra_text:
                        vorlage_type = vtype
                        break

                results.append({
                    "kvonr": kvonr,
                    "title": link.get_text(strip=True),
                    "vorlage_nr": vorlage_nr_match.group(1) if vorlage_nr_match else None,
                    "type": vorlage_type,
                    "url": f"{BASE_URL}/{link['href']}" if not link['href'].startswith('http') else link['href'],
                })

        return {
            "query": query,
            "page": page,
            "wahlperiode": WAHLPERIODEN.get(wahlperiode, {}).get("name") if wahlperiode else "all",
            "results": results,
            "result_count": len(results)
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# TOOL 11: List Gremien (Committees) - NEW
# ============================================================
@app.get("/scrape/gremien", tags=["HTML Scraper"], summary="List all Gremien (committees)",
         description="Returns a list of all Gremien (committees) including Rat, Ausschuesse, and Bezirksvertretungen with their IDs.")
def list_gremien(
    category: Optional[str] = Query(None, description="Filter by category: 'rat', 'bezirk', 'ausschuss', 'fraktion'. Leave empty for all.")
):
    """List all available Gremien from the RIS."""
    try:
        gremien = []

        # Rat (kgrnr=1)
        if not category or category == "rat":
            gremien.append({"kgrnr": 1, "name": "Rat", "category": "rat"})

        # Bezirksvertretungen
        if not category or category == "bezirk":
            soup = _html_get("gr0040.asp?__kgrtxnr=1740")
            for link in soup.find_all("a", href=lambda h: h and "kgrnr=" in h):
                match = re.search(r"kgrnr=(\d+)", link["href"])
                if match:
                    gremien.append({
                        "kgrnr": int(match.group(1)),
                        "name": link.get_text(strip=True),
                        "category": "bezirk"
                    })

        # Fachausschuesse
        if not category or category == "ausschuss":
            soup = _html_get("gr0040.asp?__kgrtxnr=1741")
            for link in soup.find_all("a", href=lambda h: h and "kgrnr=" in h):
                match = re.search(r"kgrnr=(\d+)", link["href"])
                if match:
                    gremien.append({
                        "kgrnr": int(match.group(1)),
                        "name": link.get_text(strip=True),
                        "category": "ausschuss"
                    })

        # Fraktionen
        if not category or category == "fraktion":
            soup = _html_get("gr0040.asp?__kgrtxnr=1742")
            for link in soup.find_all("a", href=lambda h: h and "kgrnr=" in h):
                match = re.search(r"kgrnr=(\d+)", link["href"])
                if match:
                    gremien.append({
                        "kgrnr": int(match.group(1)),
                        "name": link.get_text(strip=True),
                        "category": "fraktion"
                    })

        return {"gremien": gremien, "count": len(gremien)}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# TOOL 12: Get Gremium Sessions - NEW
# ============================================================
@app.get("/scrape/gremium/{kgrnr}/sessions", tags=["HTML Scraper"], summary="Get sessions for a Gremium",
         description="Returns all sessions (Sitzungen) for a specific Gremium. Supports filtering by Wahlperiode for historical data back to 2004.")
def get_gremium_sessions(
    kgrnr: int,
    wahlperiode: Optional[int] = Query(None, description="Electoral term: 1=2004-2009, 2=2009-2014, 4=2014-2020, 5=2020-2025, 7=2025-2030"),
    all_periods: bool = Query(False, description="Set to true to get sessions from all electoral terms")
):
    """Get sessions for a specific Gremium."""
    try:
        params = f"__kgrnr={kgrnr}"
        if all_periods:
            params += "&__cwpall=1"
        elif wahlperiode:
            params += f"&__cwpnr={wahlperiode}"

        soup = _html_get(f"si0041.asp?{params}")

        sessions = []
        for link in soup.find_all("a", href=lambda h: h and "__ksinr=" in h):
            match = re.search(r"__ksinr=(\d+)", link["href"])
            if match:
                ksinr = int(match.group(1))
                text = link.get_text(strip=True)
                date_match = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
                sessions.append({
                    "ksinr": ksinr,
                    "date": date_match.group(1) if date_match else None,
                    "title": text,
                    "url": f"{BASE_URL}/si0057.asp?__ksinr={ksinr}"
                })

        # Get Gremium name
        gremium_name = None
        h1 = soup.find("h1")
        if h1:
            gremium_name = h1.get_text(strip=True)

        return {
            "kgrnr": kgrnr,
            "gremium": gremium_name,
            "wahlperiode": WAHLPERIODEN.get(wahlperiode, {}).get("name") if wahlperiode else ("all" if all_periods else "current"),
            "sessions": sessions,
            "count": len(sessions)
        }
    except Exception as e:
        return {"error": str(e), "kgrnr": kgrnr}


# ============================================================
# TOOL 13: Get Session Details with Beschluesse - NEW
# ============================================================
@app.get("/scrape/session/{ksinr}", tags=["HTML Scraper"], summary="Get session details with agenda and Beschluesse",
         description="Returns detailed information about a specific session including the agenda (Tagesordnung) and all Beschluesse (decisions) made.")
def get_session_details(ksinr: int):
    """Get detailed session information including agenda and decisions."""
    try:
        soup = _html_get(f"si0057.asp?__ksinr={ksinr}")

        # Get session title and date
        title = None
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

        # Extract date from title or page
        date = None
        date_match = re.search(r"(\d{2}\.\d{2}\.\d{4})", title or "")
        if date_match:
            date = date_match.group(1)

        # Get agenda items (Tagesordnung)
        agenda_items = []
        seen_kvonr = set()  # Avoid duplicates

        for link in soup.find_all("a", href=lambda h: h and "__kvonr=" in h):
            kvonr_match = re.search(r"__kvonr=(\d+)", link["href"])
            if kvonr_match:
                kvonr = int(kvonr_match.group(1))
                if kvonr in seen_kvonr:
                    continue
                seen_kvonr.add(kvonr)

                parent = link.find_parent("tr") or link.find_parent(".smc-table-row")

                # Try to get TOP number from badge
                top_nr = None
                if parent:
                    badge = parent.find("span", class_="badge")
                    if badge:
                        badge_text = badge.get_text(strip=True)
                        # Remove "Ö" prefix (öffentlich)
                        top_nr = badge_text.replace("Ö", "").strip()

                # Get the real title - first try the title attribute of the link
                raw_title = link.get("title", "")
                real_title = ""
                if raw_title and raw_title.startswith("Vorlage anzeigen:"):
                    real_title = raw_title.replace("Vorlage anzeigen:", "").strip()

                # Fallback: look for smc-card-header-title-simple in parent row
                if not real_title and parent:
                    title_div = parent.find("div", class_="smc-card-header-title-simple")
                    if title_div:
                        real_title = title_div.get_text(strip=True)

                # Last fallback: use link text (usually just the Vorlage number)
                vorlage_nr = link.get_text(strip=True)
                if not real_title:
                    real_title = vorlage_nr

                agenda_items.append({
                    "top": top_nr,
                    "kvonr": kvonr,
                    "vorlage_nr": vorlage_nr,
                    "title": real_title,
                    "url": f"{BASE_URL}/vo0050.asp?__kvonr={kvonr}"
                })

        # Get documents (Niederschrift, etc.)
        documents = []
        for link in soup.find_all("a", href=lambda h: h and "getfile" in h):
            name = link.get_text(strip=True)
            if name:
                documents.append({
                    "name": name,
                    "url": f"{BASE_URL}/{link['href']}" if not link['href'].startswith('http') else link['href']
                })

        return {
            "ksinr": ksinr,
            "title": title,
            "date": date,
            "agenda_items": agenda_items,
            "agenda_count": len(agenda_items),
            "documents": documents,
            "url": f"{BASE_URL}/si0057.asp?__ksinr={ksinr}"
        }
    except Exception as e:
        return {"error": str(e), "ksinr": ksinr}


# ============================================================
# TOOL 14: Search Beschluesse (Decisions) - NEW
# ============================================================
@app.get("/scrape/beschluesse", tags=["HTML Scraper"], summary="Search for Beschluesse (decisions)",
         description="Search for Beschluesse (decisions) by keyword, Gremium, and time period. Allows filtering back to 2004.")
def search_beschluesse(
    query: str = Query(..., description="Search keyword"),
    gremium: Optional[int] = Query(None, description="Filter by Gremium ID (kgrnr). Use /scrape/gremien to get IDs. 1=Rat"),
    year_from: Optional[int] = Query(None, description="Start year (e.g., 2008)"),
    year_to: Optional[int] = Query(None, description="End year (e.g., 2024)"),
    vorlage_type: Optional[str] = Query(None, description="Filter by type: 'beschlussvorlage', 'antrag', 'anfrage', 'mitteilung'"),
    page: int = Query(1, ge=1, description="Results page")
):
    """Search for Beschluesse with various filters."""
    try:
        # Determine which Wahlperioden to search based on year range
        wahlperioden_to_search = []
        if year_from or year_to:
            for wpnr, wp in WAHLPERIODEN.items():
                wp_start = int(wp["start"][:4])
                wp_end = int(wp["end"][:4])
                if year_from and year_to:
                    if wp_end >= year_from and wp_start <= year_to:
                        wahlperioden_to_search.append(wpnr)
                elif year_from:
                    if wp_end >= year_from:
                        wahlperioden_to_search.append(wpnr)
                elif year_to:
                    if wp_start <= year_to:
                        wahlperioden_to_search.append(wpnr)

        all_results = []

        # If we have specific Wahlperioden, search each
        if wahlperioden_to_search:
            for wpnr in wahlperioden_to_search:
                params = {
                    "__ctext": query,
                    "__cwpnr": wpnr,
                    "__cseite": page,
                }
                if gremium:
                    params["__kgrnr"] = gremium

                r = client.get(f"{BASE_URL}/suchen01.asp", params=params)
                soup = BeautifulSoup(r.text, "html.parser")

                for link in soup.find_all("a", href=lambda h: h and "vo0050" in h):
                    kvonr_match = re.search(r"kvonr=(\d+)", link["href"])
                    if kvonr_match:
                        kvonr = int(kvonr_match.group(1))
                        text = link.get_text(strip=True)

                        # Get parent row for extra info
                        parent = link.find_parent("tr") or link.find_parent(".smc-table-row")
                        extra = parent.get_text(" ", strip=True) if parent else ""

                        # Extract type
                        found_type = None
                        type_map = {
                            "beschlussvorlage": "Beschlussvorlage",
                            "antrag": "Antrag",
                            "anfrage": "Anfrage",
                            "mitteilung": "Mitteilung"
                        }
                        for key, val in type_map.items():
                            if val.lower() in extra.lower():
                                found_type = val
                                break

                        # Filter by type if specified
                        if vorlage_type and found_type and vorlage_type.lower() not in found_type.lower():
                            continue

                        # Extract vorlage number
                        nr_match = re.search(r"(\d+/\d{4})", extra)

                        # Extract year for filtering
                        vorlage_year = None
                        if nr_match:
                            vorlage_year = int(nr_match.group(1).split("/")[1])

                        # Apply year filter
                        if vorlage_year:
                            if year_from and vorlage_year < year_from:
                                continue
                            if year_to and vorlage_year > year_to:
                                continue

                        all_results.append({
                            "kvonr": kvonr,
                            "title": text,
                            "vorlage_nr": nr_match.group(1) if nr_match else None,
                            "type": found_type,
                            "year": vorlage_year,
                            "wahlperiode": WAHLPERIODEN[wpnr]["name"],
                            "url": f"{BASE_URL}/vo0050.asp?__kvonr={kvonr}"
                        })
        else:
            # Search without Wahlperiode filter
            params = {
                "__ctext": query,
                "__cseite": page,
            }
            if gremium:
                params["__kgrnr"] = gremium

            r = client.get(f"{BASE_URL}/suchen01.asp", params=params)
            soup = BeautifulSoup(r.text, "html.parser")

            for link in soup.find_all("a", href=lambda h: h and "vo0050" in h):
                kvonr_match = re.search(r"kvonr=(\d+)", link["href"])
                if kvonr_match:
                    kvonr = int(kvonr_match.group(1))
                    text = link.get_text(strip=True)
                    parent = link.find_parent("tr") or link.find_parent(".smc-table-row")
                    extra = parent.get_text(" ", strip=True) if parent else ""

                    # Extract type
                    found_type = None
                    for val in ["Beschlussvorlage", "Antrag", "Anfrage", "Mitteilung"]:
                        if val.lower() in extra.lower():
                            found_type = val
                            break

                    if vorlage_type and found_type and vorlage_type.lower() not in found_type.lower():
                        continue

                    nr_match = re.search(r"(\d+/\d{4})", extra)

                    all_results.append({
                        "kvonr": kvonr,
                        "title": text,
                        "vorlage_nr": nr_match.group(1) if nr_match else None,
                        "type": found_type,
                        "url": f"{BASE_URL}/vo0050.asp?__kvonr={kvonr}"
                    })

        return {
            "query": query,
            "filters": {
                "gremium": gremium,
                "year_from": year_from,
                "year_to": year_to,
                "vorlage_type": vorlage_type
            },
            "page": page,
            "results": all_results,
            "result_count": len(all_results)
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# TOOL 15: Get Wahlperioden (Electoral Terms) - NEW
# ============================================================
@app.get("/scrape/wahlperioden", tags=["HTML Scraper"], summary="List available Wahlperioden (electoral terms)",
         description="Returns the available Wahlperioden (electoral terms) with their IDs for use in historical queries.")
def get_wahlperioden():
    """Return available electoral terms for filtering."""
    return {
        "wahlperioden": [
            {"id": k, **v} for k, v in sorted(WAHLPERIODEN.items())
        ],
        "note": "Use the 'id' value as the 'wahlperiode' parameter in search queries"
    }


# ============================================================
# Web Test Interface (Updated)
# ============================================================
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def web_interface():
    return HTML_PAGE


# Note: The HTML test interface uses textContent for dynamic values
# to avoid XSS concerns. This is a local development tool only.
HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cologne RIS MCP v2.0</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  .container { max-width: 1100px; margin: 0 auto; padding: 2rem; }
  h1 { font-size: 1.8rem; margin-bottom: 0.5rem; color: #38bdf8; }
  .subtitle { color: #94a3b8; margin-bottom: 2rem; }
  .grid { display: grid; grid-template-columns: 300px 1fr; gap: 1.5rem; }
  .sidebar { background: #1e293b; border-radius: 12px; padding: 1.25rem; max-height: 80vh; overflow-y: auto; }
  .sidebar h2 { font-size: 0.85rem; text-transform: uppercase; color: #64748b; margin: 1rem 0 0.5rem; }
  .tool-btn { display: block; width: 100%; padding: 0.6rem 1rem; margin-bottom: 0.4rem;
              background: #334155; border: 1px solid #475569; border-radius: 8px;
              color: #e2e8f0; cursor: pointer; text-align: left; font-size: 0.85rem; }
  .tool-btn:hover { background: #475569; border-color: #38bdf8; }
  .tool-btn.active { background: #0ea5e9; border-color: #0ea5e9; }
  .tool-btn small { display: block; color: #94a3b8; font-size: 0.7rem; }
  .tool-btn.active small { color: #bae6fd; }
  .new { background: #22c55e; color: #fff; font-size: 0.6rem; padding: 1px 4px; border-radius: 3px; margin-left: 4px; }
  .main { background: #1e293b; border-radius: 12px; padding: 1.5rem; }
  .main h2 { margin-bottom: 1rem; }
  #params { margin-bottom: 1rem; }
  .param { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; }
  .param label { width: 100px; font-size: 0.8rem; color: #94a3b8; text-align: right; }
  .param input { flex: 1; padding: 0.4rem 0.6rem; background: #0f172a; border: 1px solid #334155;
                 border-radius: 6px; color: #e2e8f0; font-size: 0.85rem; }
  .param input:focus { outline: none; border-color: #38bdf8; }
  .run { padding: 0.5rem 1.5rem; background: #0ea5e9; color: #fff; border: none; border-radius: 8px; cursor: pointer; }
  .run:hover { background: #0284c7; }
  .run:disabled { opacity: 0.5; }
  #status { font-size: 0.8rem; color: #64748b; margin-top: 0.5rem; }
  #status.ok { color: #4ade80; }
  #status.err { color: #f87171; }
  #result { margin-top: 1rem; background: #0f172a; border-radius: 8px; padding: 1rem;
            max-height: 450px; overflow: auto; font-family: monospace; font-size: 0.8rem;
            white-space: pre-wrap; word-break: break-word; }
</style>
</head>
<body>
<div class="container">
  <h1>Cologne RIS MCP v2.0</h1>
  <p class="subtitle">Council information system with historical data back to 2004</p>
  <div class="grid">
    <div class="sidebar">
      <h2>OParl API</h2>
      <button class="tool-btn" data-ep="/oparl/discover" data-p="">Discover OParl</button>
      <button class="tool-btn" data-ep="/oparl/body" data-p="">Get Body</button>
      <button class="tool-btn" data-ep="/oparl/organizations" data-p="page">Organizations</button>
      <button class="tool-btn" data-ep="/oparl/meetings" data-p="page">Meetings</button>
      <button class="tool-btn" data-ep="/oparl/papers" data-p="page">Papers</button>
      <button class="tool-btn" data-ep="/oparl/persons" data-p="page">Persons</button>
      <h2>HTML Scraper</h2>
      <button class="tool-btn active" data-ep="/scrape/vorlage/{kvonr}" data-p="kvonr">Vorlage Detail<small>With Beratungen</small></button>
      <button class="tool-btn" data-ep="/scrape/sessions" data-p="year,month">Sessions Calendar</button>
      <button class="tool-btn" data-ep="/scrape/search" data-p="query,page,wahlperiode">Search Vorlagen</button>
      <button class="tool-btn" data-ep="/scrape/gremien" data-p="category">List Gremien<span class="new">NEW</span></button>
      <button class="tool-btn" data-ep="/scrape/gremium/{kgrnr}/sessions" data-p="kgrnr,wahlperiode,all_periods">Gremium Sessions<span class="new">NEW</span></button>
      <button class="tool-btn" data-ep="/scrape/session/{ksinr}" data-p="ksinr">Session Details<span class="new">NEW</span></button>
      <button class="tool-btn" data-ep="/scrape/beschluesse" data-p="query,gremium,year_from,year_to,vorlage_type,page">Search Beschluesse<span class="new">NEW</span></button>
      <button class="tool-btn" data-ep="/scrape/wahlperioden" data-p="">Wahlperioden<span class="new">NEW</span></button>
    </div>
    <div class="main">
      <h2 id="title">Vorlage Detail</h2>
      <div id="params"></div>
      <button class="run" id="run">Run</button>
      <div id="status"></div>
      <div id="result">Select a tool and click Run</div>
    </div>
  </div>
</div>
<script>
const defaults = {page:'1',year:'2026',month:'3',kvonr:'130374',kgrnr:'1',ksinr:'33686',query:'Klima',wahlperiode:'',category:'',all_periods:'false',gremium:'',year_from:'2008',year_to:'2026',vorlage_type:''};
let ep = '/scrape/vorlage/{kvonr}', ps = ['kvonr'];

document.querySelectorAll('.tool-btn').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('.tool-btn').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    ep = b.dataset.ep;
    ps = b.dataset.p ? b.dataset.p.split(',') : [];
    document.getElementById('title').textContent = b.textContent.trim().split('\\n')[0];
    render();
  };
});

function render() {
  const c = document.getElementById('params');
  c.textContent = '';
  ps.forEach(p => {
    const d = document.createElement('div');
    d.className = 'param';
    const l = document.createElement('label');
    l.textContent = p;
    const i = document.createElement('input');
    i.id = 'p_' + p;
    i.value = defaults[p] || '';
    i.placeholder = p;
    d.appendChild(l);
    d.appendChild(i);
    c.appendChild(d);
  });
}

document.getElementById('run').onclick = async () => {
  const btn = document.getElementById('run');
  const st = document.getElementById('status');
  const res = document.getElementById('result');
  btn.disabled = true;
  st.className = '';
  st.textContent = 'Loading...';
  res.textContent = '';
  let url = ep;
  const q = new URLSearchParams();
  ps.forEach(p => {
    const v = document.getElementById('p_' + p)?.value || '';
    if (url.includes('{' + p + '}')) url = url.replace('{' + p + '}', encodeURIComponent(v));
    else if (v) q.set(p, v);
  });
  const qs = q.toString();
  try {
    const t0 = performance.now();
    const r = await fetch(qs ? url + '?' + qs : url);
    const ms = ((performance.now() - t0) / 1000).toFixed(2);
    const d = await r.json();
    st.textContent = r.status + ' in ' + ms + 's';
    st.className = r.ok ? 'ok' : 'err';
    res.textContent = JSON.stringify(d, null, 2);
  } catch (e) {
    st.textContent = 'Error';
    st.className = 'err';
    res.textContent = e.toString();
  }
  btn.disabled = false;
};

render();
</script>
</body>
</html>
"""

# ============================================================
# Mount MCP
# ============================================================
mcp = FastApiMCP(app, name="Cologne RIS")
mcp.mount_http()
