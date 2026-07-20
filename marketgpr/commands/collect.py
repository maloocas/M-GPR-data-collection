"""Kalshi Contract Catalog Builder.

Collects every Kalshi contract (ticker, name, event ticker, expiry date)
within a specified date range from the public API.  Uses both the live and
historical endpoints to cover the full time window.  Outputs a SQLite database
suitable as a lightweight index for downstream deep-data puller scripts.

Streams data page-by-page into SQLite to keep memory use low regardless
of dataset size.  Event-title enrichment is handled separately by the
``marketgpr enrich`` command.
"""

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from marketgpr.db import (DATA_DIR, CREATE_CONTRACTS,
                          accent, dim, header, highlight, info, init_db, ok, warn,
                          write_manifest)

PROD_BASE   = os.environ.get("KALSHI_API_URL", "https://api.elections.kalshi.com/trade-api/v2")
DEFAULT_START = (datetime.now(timezone.utc) - timedelta(days=730)).strftime("%Y-%m-%d")
DEFAULT_END   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
PAGE_LIMIT    = 1000
DELAY_SECONDS = 0.1
MAX_RETRIES   = 3
BACKOFF_BASE  = 2
DB_PATH       = os.path.join(DATA_DIR, "kalshi_catalog.db")
LOG_PATH      = os.path.join(DATA_DIR, "collection.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("collect")


def parse_date(value: str) -> int:
    """Accept ISO date (YYYY-MM-DD) or Unix timestamp, return Unix seconds."""
    try:
        return int(value)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return int(datetime.strptime(value, fmt)
                       .replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"Unrecognized date format: {value}")


def _url(path: str, params: dict | None = None) -> str:
    url = f"{PROD_BASE}{path}"
    if params:
        cleaned = {k: v for k, v in params.items() if v is not None}
        if cleaned:
            qs = "&".join(f"{k}={v}" for k, v in cleaned.items())
            url = f"{url}?{qs}"
    return url


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                log.debug("GET %s -> %s", url, resp.status)
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status == 429 or status >= 500:
                wait = BACKOFF_BASE ** attempt
                log.warning("HTTP %s on %s, retrying in %ss (attempt %s/%s)",
                            status, url, wait, attempt + 1, MAX_RETRIES)
                time.sleep(wait)
            else:
                log.error("HTTP %s on %s: %s", status, url,
                          exc.read().decode("utf-8", errors="replace")[:300])
                raise
        except Exception:
            wait = BACKOFF_BASE ** attempt
            log.warning("Request failed on %s, retrying in %ss (attempt %s/%s)",
                        url, wait, attempt + 1, MAX_RETRIES)
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts")


def stream_insert(conn, markets: list[dict], fetched_at: str) -> int:
    """Insert a page of markets.  Name is initially set to the ticker as
    a placeholder; enrichment fills it in later.  event_ticker is stored
    permanently so the standalone enrich command can use it.
    Returns number newly inserted."""
    rows = []
    for m in markets:
        t = m.get("ticker")
        if not t:
            continue
        et = m.get("event_ticker", "")
        close = m.get("close_time", "") or m.get("expected_expiration_time", "") or ""
        rows.append((t, t, et, close, fetched_at))
    if not rows:
        return 0
    before = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO contracts(ticker,name,event_ticker,expiry_date,fetched_at) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    return after - before


def get_cutoff() -> float:
    data = _fetch(_url("/historical/cutoff"))
    ts = data.get("market_settled_ts")
    if ts is None:
        raise RuntimeError("market_settled_ts missing from /historical/cutoff response")
    if isinstance(ts, (int, float)):
        cutoff = float(ts)
    else:
        cutoff = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    log.info("Historical cutoff: %s (%s)",
             cutoff, datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat())
    return cutoff


def _parse_close_ts(market: dict) -> float:
    raw = market.get("close_time", "")
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def collect_live(conn, start_ts: int, end_ts: int,
                 fetched_at: str, delay: float) -> int:
    """Stream live markets into the DB.  Returns number inserted."""
    inserted = 0
    cursor: str | None = None
    page = 0
    while True:
        params: dict = {"limit": PAGE_LIMIT,
                        "min_close_ts": start_ts,
                        "max_close_ts": end_ts,
                        "mve_filter": "exclude"}
        if cursor:
            params["cursor"] = cursor
        data = _fetch(_url("/markets", params))
        items = data.get("markets", [])
        page += 1
        cursor = data.get("cursor")
        n = stream_insert(conn, items, fetched_at)
        inserted += n
        if page % 10 == 0 or not cursor:
            log.info("Live: page %4s  |  %4s items  |  %6s inserted  |  total_in_db %s",
                     page, len(items), n,
                     conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0])
        if not cursor:
            break
        time.sleep(delay)
    log.info("Live complete — %s pages, %s inserted", page, inserted)
    return inserted


def collect_historical(conn, start_ts: int,
                       fetched_at: str, delay: float) -> int:
    """Stream historical markets into the DB.  Because the historical endpoint
    has no date filters, we paginate through *all* archived markets and stop
    when close_time drops below start_ts (results are ordered desc by date)."""
    inserted = 0
    cursor: str | None = None
    page = 0
    while True:
        params: dict = {"limit": PAGE_LIMIT, "mve_filter": "exclude"}
        if cursor:
            params["cursor"] = cursor
        data = _fetch(_url("/historical/markets", params))
        items = data.get("markets", [])
        page += 1
        cursor = data.get("cursor")

        filtered = [m for m in items if _parse_close_ts(m) >= start_ts]
        skipped = len(items) - len(filtered)
        n = stream_insert(conn, filtered, fetched_at)
        inserted += n
        earliest = min((_parse_close_ts(m) for m in items), default=0)

        if page % 10 == 0 or not cursor:
            log.info("Hist: page %4s  |  %4s items  |  kept %4s  |  %6s inserted  |  earliest %s  |  total_in_db %s",
                     page, len(items), len(filtered), n,
                     datetime.fromtimestamp(earliest, tz=timezone.utc).strftime("%Y-%m-%d") if earliest else "?",
                     conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0])

        if not cursor:
            break
        if skipped == len(items) and len(items) > 0:
            log.info("Historical: all remaining markets are before %s, stopping",
                     datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d"))
            break
        time.sleep(delay)

    log.info("Historical complete — %s pages, %s inserted", page, inserted)
    return inserted


def register_args(parser: argparse.ArgumentParser):
    parser.add_argument("--start", type=parse_date, default=parse_date(DEFAULT_START),
                        help="Beginning of collection window (ISO date or Unix timestamp)")
    parser.add_argument("--end",   type=parse_date, default=parse_date(DEFAULT_END),
                        help="End of collection window (ISO date or Unix timestamp)")
    parser.add_argument("--db",    default=DB_PATH,
                        help="Path to output SQLite database")
    parser.add_argument("--delay", type=float, default=DELAY_SECONDS,
                        help="Pause between API page requests (seconds)")


def run(args: argparse.Namespace):
    p = lambda s: print(s, flush=True)

    delay = args.delay
    start_ts = int(args.start)
    end_ts   = int(args.end)

    p("")
    p(header("=== MarketGPR  ·  Contract Catalog Builder ==="))
    p(accent(f"API:      {PROD_BASE}"))
    p(accent(f"Range:    {datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime('%Y-%m-%d')}"
             f"  →  {datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime('%Y-%m-%d')}"))
    p(accent(f"DB:       {args.db}"))
    p(accent(f"Delay:    {delay}s/page"))
    p("")

    started = time.time()
    conn = init_db(args.db)
    existing = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    p(info(f"Existing rows in DB: {existing:,}"))

    cutoff = get_cutoff()
    fetched_at = datetime.now(timezone.utc).isoformat()
    total_inserted = 0

    if end_ts >= cutoff:
        live_start = max(start_ts, int(cutoff))
        live_end   = end_ts
        p("")
        p(highlight(">>> Phase 1: LIVE   "
                    f"{datetime.fromtimestamp(live_start, tz=timezone.utc).strftime('%Y-%m-%d')}"
                    f" → {datetime.fromtimestamp(live_end, tz=timezone.utc).strftime('%Y-%m-%d')}"))
        total_inserted += collect_live(conn, live_start, live_end, fetched_at, delay)

    if start_ts < cutoff:
        p("")
        p(highlight(f">>> Phase 2: HIST  "
                    f"{datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime('%Y-%m-%d')}"
                    f" → cutoff"))
        total_inserted += collect_historical(conn, start_ts, fetched_at, delay)

    row_count = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    conn.close()

    duration = time.time() - started

    p("")
    p(ok(f"Done: {row_count:,} total rows  |  {total_inserted:,} inserted this run  |  {duration:.1f}s"))
    manifest_path = write_manifest(args.db, start_ts, end_ts, duration, PROD_BASE)
    p(dim(f"Manifest: {manifest_path}"))
    p("")
