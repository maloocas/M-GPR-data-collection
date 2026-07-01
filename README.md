# MarketGPR

**Two CLI scripts for building and filtering a Kalshi prediction-market contract index — designed for geopolitical risk research.**

---

## Quick start

```bash
# 1. Collect contracts from the Kalshi API into a SQLite catalog
python collect.py --start 2026-01-01 --end 2026-07-01

# 2. Filter an existing catalog by keywords and regex
python clean.py --db data/kalshi_catalog.db --config filters.json

# 3. Dry-run to preview what would be removed
python clean.py --db data/kalshi_catalog.db --config filters.json --dry-run
```

See `python collect.py --help` or `python clean.py --help` for all options.

---

## Commands

### `collect.py` — build the catalog

Pulls every Kalshi contract (ticker, name, expiry date, event ticker) from both
the live and historical API endpoints and writes them into a SQLite database.

```bash
# Full 2-year window (default)
python collect.py

# Custom date range
python collect.py --start 2026-01-01 --end 2026-07-01

# Unix timestamps work too
python collect.py --start 1700000000 --end 1710000000

# Custom output path
python collect.py --db my_catalog.db

# Skip event-title enrichment (faster, leaves name as event_ticker)
python collect.py --no-enrich

# Rate-limit delay between API pages (seconds)
python collect.py --delay 0.5
```

| Arg | Default | Description |
|-----|---------|-------------|
| `--start` | 2 years ago | Begin of collection window (YYYY-MM-DD or Unix timestamp) |
| `--end` | today | End of collection window |
| `--db` | `data/kalshi_catalog.db` | Output SQLite database path |
| `--delay` | `0.1` | Pause between API pages (seconds) |
| `--no-enrich` | false | Skip event-title enrichment pass |

**Pipeline phases:**
| Phase | Source | Covers |
|-------|--------|--------|
| **Live** | `GET /markets` | Contracts closing in the ~90-day live window |
| **Historical** | `GET /historical/markets` | Archived (settled) contracts |
| **Enrich** (optional) | `GET /events` | Replaces ticker placeholders with event titles |

Collection is idempotent — `INSERT OR IGNORE` means partial runs resume cleanly.

### `clean.py` — filter the catalog

Filters an existing catalog by keywords and regex patterns. Writes matching rows
to a new database — the original is never modified.

```bash
# Use full config file
python clean.py --db data/kalshi_catalog.db --config filters.json

# Inline keywords (comma-separated)
python clean.py --db data/kalshi_catalog.db --keep "war,election" --remove "NBA,NFL"

# Dry run — preview counts without writing
python clean.py --db data/kalshi_catalog.db --config filters.json --dry-run

# Custom output path
python clean.py --db data/kalshi_catalog.db --output cleaned.db
```

| Arg | Default | Description |
|-----|---------|-------------|
| `--db` | *(required)* | Input SQLite database |
| `--config` | `filters.json` | JSON file with keep/remove rules |
| `--keep` | *(none)* | Comma-separated keywords — keep rows matching ANY |
| `--remove` | *(none)* | Comma-separated keywords — drop rows matching ANY |
| `--output` | `<input>_cleaned.db` | Output database path |
| `--dry-run` | false | Preview counts and samples without writing |

---

## Filter config format (`filters.json`)

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
| `{"pattern":"above \\d+\\.\\d+","type":"regex"}` | name | REGEXP | `name REGEXP 'above \\d+\\.\\d+'` |
| `{"pattern":"MENTION","field":"ticker"}` | ticker | LIKE | `ticker LIKE '%MENTION%'` |
| `{"pattern":"^KX.*T$","field":"ticker","type":"regex"}` | ticker | REGEXP | `ticker REGEXP '^KX.*T$'` |

All matching is case-insensitive. Inline keywords from `--keep` and `--remove` are
merged with the config file entries.

---

## Built-in filter categories

`filters.json` ships with 76 exclusion patterns across 7 categories:

| Category | Examples |
|----------|----------|
| Mention Markets | ticker `MENTION`, regex `What will .+ say` |
| Sports | NBA, NFL, MLB, NHL, UFC, ATP, WTA, F1, Valorant, CS2 |
| Crypto | Bitcoin, Ethereum, BTC, ETH, SOL, DOGE, NFT, DeFi |
| Entertainment | Oscar, Grammy, Netflix, Spotify, TikTok, album, concert |
| Weather | hurricane, tornado, earthquake, temperature, snowfall |
| Commodities | WTI, Brent, gold, silver, crude oil, settlement price |
| Financial | CPI, PPI, GDP, unemployment, NASDAQ, VIX, SOFR |

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

## File structure

```
M-GPR-data-collection/
├── collect.py               # Catalog builder (Kalshi API → DB)
├── clean.py                 # Contract cleaner (keyword + regex filters)
├── db.py                    # Shared module (schema, connections, metadata)
├── filters.json             # Curated exclusion rules (76 patterns)
├── data/                    # Runtime output (DBs, logs, manifests)
│   └── .gitkeep
├── README.md
└── .gitignore
```

---

## Requirements

- Python 3.8+ (zero external dependencies — stdlib only)

Scale estimates for collection:

| Range | Est. rows | Est. DB size | Est. time | Est. traffic |
|-------|-----------|-------------|-----------|-------------|
| 1 day | ~40,000 | ~13 MB | ~40 s | ~50 MB |
| 1 month | ~1.2M | ~400 MB | ~20 min | ~350 MB |
| 1 year | ~15M | ~4.5 GB | ~4 hours | ~7.5 GB |
| 2 years | ~30M | ~9 GB | ~8 hours | ~15 GB |

MVE (multivariate event) combo markets are excluded by default — they account for ~89% of raw API volume.
