#!/usr/bin/env python3
"""Shared database helpers and ANSI colour palette for MarketGPR tools.

Provides:
    * Canonical contracts table schema (single source of truth)
    * Connection management (writable, read-only with REGEXP)
    * Manifest generation
    * ANSI colour constants (dark-blue palette)
"""

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ── Schema constants ───────────────────────────────────────────────────────

CREATE_CONTRACTS = """CREATE TABLE IF NOT EXISTS contracts (
    ticker        TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    expiry_date   TEXT NOT NULL,
    fetched_at    TEXT NOT NULL
)"""

CREATE_EXPIRY_IDX  = "CREATE INDEX IF NOT EXISTS idx_expiry ON contracts(expiry_date)"

CREATE_ENRICH_TEMP = """CREATE TEMP TABLE IF NOT EXISTS _enrich (
    ticker       TEXT PRIMARY KEY,
    event_ticker TEXT NOT NULL
)"""

# ── ANSI colour palette (dark-blue) ────────────────────────────────────────

BOLD  = "\033[1m"
DIM   = "\033[2m"
RESET = "\033[0m"

BLUE        = "\033[34m"
BRIGHT_BLUE = "\033[94m"
CYAN        = "\033[36m"
GREEN       = "\033[32m"
YELLOW      = "\033[33m"
RED         = "\033[31m"
MAGENTA     = "\033[35m"


def header(text: str) -> str:
    return f"{BOLD}{BRIGHT_BLUE}{text}{RESET}"


def info(text: str) -> str:
    return f"{CYAN}{text}{RESET}"


def ok(text: str) -> str:
    return f"{GREEN}{text}{RESET}"


def warn(text: str) -> str:
    return f"{YELLOW}{text}{RESET}"


def err(text: str) -> str:
    return f"{RED}{text}{RESET}"


def accent(text: str) -> str:
    return f"{BLUE}{text}{RESET}"


def dim(text: str) -> str:
    return f"{DIM}{text}{RESET}"


def bold(text: str) -> str:
    return f"{BOLD}{text}{RESET}"


def highlight(text: str) -> str:
    return f"{BOLD}{BRIGHT_BLUE}{text}{RESET}"


# ── Connection management ──────────────────────────────────────────────────

def init_db(db_path: str) -> sqlite3.Connection:
    """Create / open a writable SQLite connection with performance PRAGMAs."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")
    conn.execute(CREATE_CONTRACTS)
    conn.execute(CREATE_EXPIRY_IDX)
    conn.commit()
    return conn


def connect_readonly(db_path: str) -> sqlite3.Connection:
    """Open a read-only SQLite connection.  REGEXP is registered on it."""
    abs_path = os.path.abspath(db_path)
    uri = f"file:{abs_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    _register_regex(conn)
    return conn


def _register_regex(conn: sqlite3.Connection) -> None:
    """Register a case-insensitive regexp() user function."""

    def _regexp(pattern: str, text: Optional[str]) -> bool:
        if text is None:
            return False
        try:
            return re.search(pattern, text, re.IGNORECASE) is not None
        except re.error:
            return False

    conn.create_function("REGEXP", 2, _regexp, deterministic=True)


# ── Manifest ───────────────────────────────────────────────────────────────

def write_manifest(db_path: str, start_ts: int, end_ts: int,
                   duration: float, api_base: str) -> str:
    """Write collection_manifest.json into DATA_DIR.  Return path."""
    sha = hashlib.sha256()
    with open(db_path, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)

    conn = sqlite3.connect(db_path)
    row_count = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    conn.close()

    manifest = {
        "api_base":         api_base,
        "start_ts":         start_ts,
        "end_ts":           end_ts,
        "start_iso":        datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
        "end_iso":          datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
        "total_rows":       row_count,
        "duration_seconds": round(duration, 1),
        "db_sha256":        sha.hexdigest(),
        "finished_at":      datetime.now(timezone.utc).isoformat(),
    }

    manifest_path = os.path.join(DATA_DIR, "collection_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest_path
