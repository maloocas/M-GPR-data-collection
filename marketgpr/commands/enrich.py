"""MarketGPR Enricher — Fetch event titles and update contract names.

Reads a SQLite database of Kalshi contracts, looks up event titles from the
API for every distinct event_ticker, and writes an enriched copy to a new file.
Never modifies the original database.
"""

import argparse
import os
import sqlite3
import sys
import time

from marketgpr.db import (CREATE_CONTRACTS, CREATE_EXPIRY_IDX,
                          accent, dim, err, header, highlight, info, ok, warn,
                          connect_readonly, build_url, fetch_json)

BATCH_SIZE = 10_000


def register_args(parser: argparse.ArgumentParser):
    parser.add_argument("--db", required=True,
                        help="Path to input SQLite database")
    parser.add_argument("--delay", type=float, default=0.1,
                        help="Pause between API batch requests (seconds)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Skip confirmation prompt")


def resolve_output_path(input_db: str) -> str:
    """Determine output path: enriched_[dbname] in the same folder."""
    db_dir = os.path.dirname(os.path.abspath(input_db))
    base = os.path.splitext(os.path.basename(input_db))[0]
    return os.path.join(db_dir, f"enriched_{base}.db")


def enrich_database(input_db: str, output_db: str, delay: float) -> int:
    """Read source DB, fetch event titles, write enriched copy.
    Streams data in batches to keep memory use low.
    Returns number of rows updated."""
    conn = connect_readonly(input_db)

    try:
        rows = conn.execute(
            "SELECT DISTINCT event_ticker FROM contracts WHERE event_ticker != ''"
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        p = lambda s: print(s, flush=True)
        p(err("Error: database lacks an event_ticker column. "
              "Re-collect with the updated 'marketgpr collect' command."))
        sys.exit(1)

    tickers = [r[0] for r in rows if r[0]]
    total_rows = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    conn.close()

    p = lambda s: print(s, flush=True)

    titles: dict[str, str] = {}
    if tickers:
        p(accent(f"Found {len(tickers):,} unique event tickers"))
        total_batches = (len(tickers) + 99) // 100
        for i in range(0, len(tickers), 100):
            batch = tickers[i:i + 100]
            params = {"tickers": ",".join(batch), "limit": 200}
            data = fetch_json(build_url("/events", params))
            for event in data.get("events", []):
                t = event.get("event_ticker", "")
                title = event.get("title", "")
                if t and title:
                    titles[t] = title
            batch_num = i // 100 + 1
            p(dim(f"  Batch {batch_num}/{total_batches}: "
                  f"{len(batch)} tickers → {len(titles)} titles so far"))
            if i + 100 < len(tickers):
                time.sleep(delay)
        p(ok(f"Fetched {len(titles):,} event titles"))
    else:
        p(info("No event tickers to enrich — copying unchanged."))

    if os.path.exists(output_db):
        os.remove(output_db)

    out_conn = sqlite3.connect(output_db)
    out_conn.execute("PRAGMA journal_mode=WAL")
    out_conn.execute("PRAGMA synchronous=NORMAL")
    out_conn.execute("PRAGMA cache_size=-65536")
    out_conn.execute(CREATE_CONTRACTS)
    out_conn.execute(CREATE_EXPIRY_IDX)

    src_conn = connect_readonly(input_db)
    cursor = src_conn.execute(
        "SELECT ticker, name, event_ticker, expiry_date, fetched_at "
        "FROM contracts"
    )

    updated = 0
    batch_count = 0
    while True:
        batch = cursor.fetchmany(BATCH_SIZE)
        if not batch:
            break
        enriched_rows = []
        for ticker, name, event_ticker, expiry_date, fetched_at in batch:
            new_name = titles.get(event_ticker, name)
            enriched_rows.append((ticker, new_name, event_ticker, expiry_date, fetched_at))
            if new_name != name:
                updated += 1
        out_conn.executemany(
            "INSERT INTO contracts(ticker, name, event_ticker, expiry_date, fetched_at) "
            "VALUES (?,?,?,?,?)",
            enriched_rows,
        )
        batch_count += 1
        if batch_count % 100 == 0:
            p(dim(f"  Processing batch {batch_count:,} ..."))
    out_conn.commit()
    out_conn.close()
    src_conn.close()

    return updated


def run(args: argparse.Namespace):
    p = lambda s: print(s, flush=True)

    if not os.path.isfile(args.db):
        p(warn(f"Error: database not found: {args.db}"))
        sys.exit(1)

    output_db = resolve_output_path(args.db)

    p("")
    p(header("=== MarketGPR  ·  Contract Enricher ==="))
    p(accent(f"Input:  {args.db}"))
    p(accent(f"Output: {output_db}"))
    p(accent(f"Delay:  {args.delay}s/batch"))
    p("")

    if not args.yes:
        try:
            response = input(f"{highlight('Proceed?')} [y/N] ").strip().lower()
            if response not in ("y", "yes"):
                p(warn("Aborted."))
                sys.exit(0)
        except (KeyboardInterrupt, EOFError):
            p("")
            p(warn("Aborted."))
            sys.exit(0)

    updated = enrich_database(args.db, output_db, args.delay)

    out_conn = sqlite3.connect(output_db)
    total = out_conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    out_conn.close()

    p("")
    if updated == 0:
        p(info(f"No changes — {total:,} rows unchanged"))
    else:
        p(ok(f"Done: {total:,} total rows  |  {updated:,} names enriched"))

    p(ok(f"Wrote: {output_db}"))
    p("")
