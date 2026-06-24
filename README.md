# MarketGPR

**A zero-dependency pipeline for building and filtering a Kalshi prediction-market contract index — designed for geopolitical risk research.**

Two scripts.  One shared module.  < 1 000 lines of Python.  No pip install, no venv, no requirements.txt.

```
┌──────────────┐        ┌──────────────┐
│  collect.py  │───────▶│  clean.py    │
│  Catalogger  │   DB   │  Cleaner     │
│  (Kalshi     │───────▶│  (keyword +  │
│   API → DB)  │        │   regex      │
└──────────────┘        │   filters)   │
                        └──────┬───────┘
                               │
                        ┌──────▼───────┐
                        │  cleaned.db  │
                        │  (geopoli-   │
                        │   tical only)│
                        └──────────────┘
```

---

## Quick start

```bash
# 1. Build the catalog (fetch all Kalshi contracts)
python collect.py --start 2026-01-01

# 2. Preview what the built-in filters would remove
python clean.py --db data/kalshi_catalog.db --config filters.json --dry-run

# 3. Apply filters
python clean.py --db data/kalshi_catalog.db --config filters.json
```

All output lands in `data/`.  The original database is never modified.

---

## Tools

### `collect.py` — Contract Catalog Builder

Fetches every Kalshi contract (ticker, name, expiry date) within a date range from the public Trade API.  Three-phase streaming pipeline:

| Phase | Endpoint | What it does |
|-------|----------|--------------|
| **Live** | `GET /markets` | Fetches contracts closing in the ~90-day live window |
| **Historical** | `GET /historical/markets` | Fetches older archived (settled) contracts |
| **Enrich** | `GET /events` | Replaces ticker placeholders with human-readable event titles |

**Streaming architecture** — data is inserted page-by-page into SQLite.  Memory stays flat regardless of dataset size.  `INSERT OR IGNORE` makes it idempotent — kill and re-run safely.

```bash
python collect.py                                 # last 2 years
python collect.py --start 2025-01-01 --end 2025-06-30  # 6-month window
python collect.py --start 1700000000 --end 1710000000  # unix timestamps
python collect.py --no-enrich                           # skip event titles (faster)
python collect.py --delay 0.2                           # slower, safer on rate limits
```

**Scale estimates:**

| Range | Est. rows | Est. DB size | Est. time | Est. traffic |
|-------|-----------|-------------|-----------|-------------|
| 1 day | ~40 000 | ~13 MB | ~40 s | ~50 MB |
| 1 month | ~1.2M | ~400 MB | ~20 min | ~350 MB |
| 1 year | ~15M | ~4.5 GB | ~4 hours | ~7.5 GB |
| 2 years | ~30M | ~9 GB | ~8 hours | ~15 GB |

MVE (multivariate event) combo markets are excluded by default — they account for ~89 % of raw API volume.

---

### `clean.py` — Contract Cleaner

Filters a Kalshi catalog database by keywords and regex.  Strips out unwanted market categories while preserving geopolitically-relevant contracts.

```bash
# Preview (safe, no writes)
python clean.py --db data/kalshi_catalog.db --config filters.json --dry-run

# Apply filters
python clean.py --db data/kalshi_catalog.db --config filters.json

# Ad-hoc: remove sports + crypto by keyword
python clean.py --db data/kalshi_catalog.db --remove "NBA,NFL,BTC,ETH"

# Extract only elections
python clean.py --db data/kalshi_catalog.db \
  --keep "election,congress,senate,president,parliament,war,conflict,tariff" \
  --output elections.db
```

**Filtering modes:**

| Mode | How | Example |
|------|-----|---------|
| Config file | `--config filters.json` | Curated rules, 76 patterns |
| CLI keywords | `--keep / --remove` | `--remove "NBA,NFL"` |
| CLI regex | `--keep-regex / --remove-regex` | `--remove-regex "above \d+\.\d+"` |
| Mix both | config + CLI flags | CLI rules append to config |

**Processing order:**

```
Original rows
    │
    ▼
[Keep filters]  →  Narrow to rows matching ANY keep keyword
    │               (skipped if no keep rules)
    ▼
[Remove filters] →  Drop rows matching ANY remove keyword/pattern
    │
    ▼
Final rows  →  Written to output database
```

---

## Built-in filter categories

`filters.json` ships with 76 exclusion patterns across 7 categories:

| Category | Scope | Examples |
|----------|-------|----------|
| Mention Markets | Word-guessing bets | ticker `MENTION`, regex `What will .+ say` |
| Sports | Zero geopolitical content | NBA, NFL, MLB, NHL, UFC, ATP, WTA, F1, Valorant, CS2 |
| Crypto | Financial speculation | Bitcoin, Ethereum, BTC, ETH, SOL, DOGE, NFT, DeFi |
| Entertainment | Cultural markets | Oscar, Grammy, Netflix, Spotify, TikTok, album, concert |
| Weather | Natural phenomena | hurricane, tornado, earthquake, temperature, snowfall |
| Commodities | Price-level binaries | WTI, Brent, gold, silver, crude oil, settlement price |
| Financial | Macro data releases | CPI, PPI, GDP, unemployment, NASDAQ, VIX, SOFR |

---

## Filter config format

`filters.json`:

```json
{
  "keep": [],
  "remove": [
    "NBA",
    {"pattern": "above \\d+\\.\\d+", "type": "regex"},
    {"pattern": "MENTION", "field": "ticker"}
  ]
}
```

| Entry form | Field | Match type | SQL equivalent |
|-----------|-------|-----------|----------------|
| `"Bitcoin"` | name | LIKE | `name LIKE '%Bitcoin%'` |
| `{"pattern":"BTC"}` | name | LIKE | `name LIKE '%BTC%'` |
| `{"pattern":"above \\d+\\.\\d+","type":"regex"}` | name | REGEXP | `name REGEXP 'above \d+\.\d+'` |
| `{"pattern":"MENTION","field":"ticker"}` | ticker | LIKE | `ticker LIKE '%MENTION%'` |
| `{"pattern":"^KX.*T$","field":"ticker","type":"regex"}` | ticker | REGEXP | `ticker REGEXP '^KX.*T$'` |

All matching is case-insensitive.

---

## Database schema

```sql
contracts (
    ticker       TEXT PRIMARY KEY,   -- e.g. KXBTC-26JUN21-T100000
    name         TEXT NOT NULL,      -- e.g. "BTC price on Jun 21, 2026?"
    expiry_date  TEXT NOT NULL,      -- e.g. "2026-06-21T12:00:00Z"
    fetched_at   TEXT NOT NULL       -- when this row was collected
)

INDEX idx_expiry ON contracts(expiry_date)
```

---

## Downstream API usage

The `ticker` column is your key for deep-data puller scripts:

```bash
# OHLC candlesticks
GET /markets/{ticker}/candlesticks?start_ts=...&end_ts=...&period_interval=60

# Trade history
GET /markets/trades?ticker={ticker}&min_ts=...&max_ts=...

# Market detail / orderbook
GET /markets/{ticker}
GET /markets/{ticker}/orderbook

# Historical (settled >90 days ago)
GET /historical/markets/{ticker}
GET /historical/markets/{ticker}/candlesticks
```

---

## File structure

```
MarketGPR Public/
├── collect.py             # Catalog builder (Kalshi API → DB)
├── clean.py               # Contract cleaner (keyword + regex filters)
├── db.py                  # Shared module (schema, connections, colours)
├── filters.json           # Curated exclusion rules (76 patterns)
├── .gitignore
├── README.md
└── data/                  # Runtime output (DBs, logs, manifests)
    └── .gitkeep
```

---

## Safety guarantees

- **Input is read-only** — cleaner opens source DB via `file:path?mode=ro`
- **Output is always a new file** — defaults to `data/`
- **Idempotent** — `INSERT OR IGNORE` means partial collections resume cleanly
- **Interactive confirmation** — cleaner prompts `[y/N]` before writing (skip with `-y`)
- **Dry-run mode** — `--dry-run` shows exact row counts and samples without writing

---

## Dependencies

**Zero.**  Python 3.8+ standard library only — `sqlite3`, `urllib.request`, `argparse`, `json`, `re`, `logging`, `hashlib`.

---

## Coming soon

- **Interactive TUI** — `ui.py` with [Textual](https://textual.textualize.io/) providing guided setup, filter configuration, progress bars, and live previews.  Both scripts are already built as importable modules so the TUI layer calls them directly.
