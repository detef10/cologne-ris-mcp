#!/usr/bin/env python3
"""
Indexer for Cologne RIS Beschlüsse
Fetches new Beschlüsse and tags them using hybrid approach.
Can be run manually or as a cron job.
"""

import asyncio
import httpx
from datetime import datetime, timedelta
from typing import List, Dict
import argparse

from tag_database import init_database, tag_beschluss, tag_beschluss_async, get_stats, BEZIRKE

MCP_BASE_URL = "http://127.0.0.1:8766"


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


async def index_gremium(
    kgrnr: int,
    wahlperiode: int = 7,
    max_sessions: int = 10,
    use_llm: bool = True,
    verbose: bool = False
) -> Dict:
    """Index all Beschlüsse for a committee."""
    gremium_name = BEZIRKE.get(kgrnr, f"Gremium {kgrnr}")
    print(f"\n📋 Indexing: {gremium_name} (kgrnr={kgrnr})")

    stats = {"sessions": 0, "beschluesse": 0, "new": 0, "existing": 0}

    # Fetch sessions
    sessions = await fetch_gremium_sessions(kgrnr, wahlperiode)
    print(f"   Found {len(sessions)} sessions")

    for session in sessions[:max_sessions]:
        ksinr = session.get("ksinr")
        session_date = session.get("date")
        stats["sessions"] += 1

        if verbose:
            print(f"   📅 Session {ksinr} ({session_date})")

        # Fetch session details
        details = await fetch_session_details(ksinr)
        agenda_items = details.get("agenda_items", [])

        for item in agenda_items:
            kvonr = item.get("kvonr")
            title = item.get("title", "")
            vorlage_nr = item.get("vorlage_nr", "")
            url = item.get("url", "")

            if not kvonr or not title:
                continue

            stats["beschluesse"] += 1

            # Tag the Beschluss (use async version if LLM enabled)
            if use_llm:
                result = await tag_beschluss_async(
                    kvonr=kvonr,
                    vorlage_nr=vorlage_nr,
                    title=title,
                    gremium_id=kgrnr,
                    session_id=ksinr,
                    session_date=session_date,
                    url=url,
                    use_llm=True
                )
            else:
                result = tag_beschluss(
                    kvonr=kvonr,
                    vorlage_nr=vorlage_nr,
                    title=title,
                    gremium_id=kgrnr,
                    session_id=ksinr,
                    session_date=session_date,
                    url=url,
                    use_llm=False
                )

            if result["status"] == "created":
                stats["new"] += 1
                if verbose:
                    tags = result.get("tags", [])
                    print(f"      ✅ {vorlage_nr}: {title[:50]}... [{', '.join(tags)}]")
            else:
                stats["existing"] += 1

        # Small delay to be nice to the server
        await asyncio.sleep(0.5)

    print(f"   ✓ {stats['new']} new, {stats['existing']} existing")
    return stats


async def index_all_bezirksvertretungen(
    wahlperiode: int = 7,
    max_sessions: int = 10,
    use_llm: bool = True,
    verbose: bool = False
) -> Dict:
    """Index all Bezirksvertretungen."""
    total_stats = {"sessions": 0, "beschluesse": 0, "new": 0, "existing": 0}

    # All Bezirksvertretungen (20-28) plus Rat (1)
    gremien = [22, 20, 21, 23, 24, 25, 26, 27, 28, 1]  # Start with Lindenthal

    for kgrnr in gremien:
        stats = await index_gremium(kgrnr, wahlperiode, max_sessions, use_llm, verbose)
        for key in total_stats:
            total_stats[key] += stats[key]

    return total_stats


async def index_recent(days: int = 30, use_llm: bool = True, verbose: bool = False) -> Dict:
    """Index only recent sessions (last N days)."""
    print(f"\n🕐 Indexing sessions from the last {days} days...")

    # For now, we'll index recent sessions from all Bezirksvertretungen
    # A smarter approach would check the session calendar
    return await index_all_bezirksvertretungen(
        wahlperiode=7,
        max_sessions=5,  # Fewer sessions when doing recent only
        use_llm=use_llm,
        verbose=verbose
    )


def main():
    parser = argparse.ArgumentParser(description="Index Cologne RIS Beschlüsse")
    parser.add_argument(
        "--gremium", "-g", type=int,
        help="Index specific Gremium by ID (e.g., 22 for Lindenthal)"
    )
    parser.add_argument(
        "--all", "-a", action="store_true",
        help="Index all Bezirksvertretungen"
    )
    parser.add_argument(
        "--recent", "-r", type=int, metavar="DAYS",
        help="Index only recent sessions (last N days)"
    )
    parser.add_argument(
        "--sessions", "-s", type=int, default=10,
        help="Max sessions per Gremium (default: 10)"
    )
    parser.add_argument(
        "--wahlperiode", "-w", type=int, default=7,
        help="Wahlperiode to index (default: 7 = current)"
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Disable LLM tagging (rule-based only)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Show database statistics"
    )
    parser.add_argument(
        "--init", action="store_true",
        help="Initialize/reset the database"
    )

    args = parser.parse_args()

    # Initialize database
    if args.init:
        init_database()
        print("Database initialized.")
        return

    # Show stats
    if args.stats:
        stats = get_stats()
        print("\n📊 Database Statistics:")
        print(f"   Total Beschlüsse: {stats['total_beschluesse']}")
        print(f"   Total Tags: {stats['total_tags']}")
        print(f"   By tagging method: {stats['by_method']}")
        print(f"   By Gremium: {stats['by_gremium']}")
        print(f"   Database: {stats['database_path']}")
        return

    # Make sure database exists
    init_database()

    use_llm = not args.no_llm

    # Run indexing
    if args.gremium:
        asyncio.run(index_gremium(
            args.gremium,
            args.wahlperiode,
            args.sessions,
            use_llm,
            args.verbose
        ))
    elif args.recent:
        asyncio.run(index_recent(args.recent, use_llm, args.verbose))
    elif args.all:
        asyncio.run(index_all_bezirksvertretungen(
            args.wahlperiode,
            args.sessions,
            use_llm,
            args.verbose
        ))
    else:
        # Default: index Lindenthal as a test
        print("No target specified. Use --help for options.")
        print("Running quick test with Bezirksvertretung Lindenthal...")
        asyncio.run(index_gremium(22, 7, 3, use_llm, True))

    # Show final stats
    print("\n" + "="*50)
    stats = get_stats()
    print(f"📊 Database now contains {stats['total_beschluesse']} Beschlüsse with {stats['total_tags']} unique tags")


if __name__ == "__main__":
    main()
