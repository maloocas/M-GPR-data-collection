# MarketGPR CLI Guide

Two zero-dependency CLI scripts for building and filtering a Kalshi prediction-market contract index, designed for geopolitical risk research.

---

## Quick Start

```bash
# 1. Collect contracts (default: 2-year window)
python collect.py

# 2. Preview what gets filtered
python clean.py --db data/kalshi_catalog.db --config filters.json --dry-run

# 3. Apply filters
python clean.py --db data/kalshi_catalog.db --config filters.json
```

**Requirements:** Python 3.8+ (no pip install — stdlib only).

---

## `collect.py` — Catalog Builder

Pulls every Kalshi contract from live and historical API endpoints into a SQLite database. Idempotent — safe to re-run.

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--start` | ISO date or unix ts | 2 years ago | Begin of collection window |
| `--end` | ISO date or unix ts | today | End of collection window |
| `--db` | `str` | `data/kalshi_catalog.db` | Output SQLite path |
| `--delay` | `float` | `0.1` | Pause between API pages (seconds) |
| `--no-enrich` | `store_true` | `false` | Skip event-title enrichment pass |

### Date Formats

- `2026-01-01`
- `01/01/2026`
- `2026-01-01T12:00:00`
- `2026-01-01T12:00:00Z`
- Unix timestamp: `1700000000`

### Examples

```bash
python collect.py                                                    # Full 2-year window
python collect.py --start 2026-01-01 --end 2026-07-01                # Custom range
python collect.py --start 1700000000 --end 1710000000                # Unix timestamps
python collect.py --db my_catalog.db                                 # Custom output path
python collect.py --no-enrich                                        # Skip event-title enrichment
python collect.py --delay 0.5                                        # Slower rate limit
```

### Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `KALSHI_API_URL` | `https://api.elections.kalshi.com/trade-api/v2` | Override API base URL |

### Scale Estimates

| Range | Rows | DB Size | Time | Traffic |
|-------|------|---------|------|---------|
| 1 day | ~40K | ~13 MB | ~40 s | ~50 MB |
| 1 month | ~1.2M | ~400 MB | ~20 min | ~350 MB |
| 1 year | ~15M | ~4.5 GB | ~4 h | ~7.5 GB |
| 2 years | ~30M | ~9 GB | ~8 h | ~15 GB |

---

## `clean.py` — Contract Filter

Filters a catalog by keyword/regex rules. Writes matching rows to a **new** database — never modifies the original.

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--db` | `str` | **required** | Input SQLite database |
| `--config` | `str` | `filters.json` | JSON config with `keep`/`remove` arrays |
| `--keep` | `str` | — | Comma-separated keywords to **keep** |
| `--remove` | `str` | — | Comma-separated keywords to **remove** |
| `--keep-regex` | `str` | — | Comma-separated regex patterns to **keep** |
| `--remove-regex` | `str` | — | Comma-separated regex patterns to **remove** |
| `--ticker-keep` | `str` | — | Ticker LIKE keywords to **keep** |
| `--ticker-remove` | `str` | — | Ticker LIKE keywords to **remove** |
| `--ticker-keep-regex` | `str` | — | Ticker REGEXP patterns to **keep** |
| `--ticker-remove-regex` | `str` | — | Ticker REGEXP patterns to **remove** |
| `--output` | `str` | `<db>_cleaned.db` in `data/` | Output database path |
| `--dry-run` | `store_true` | `false` | Preview counts/samples without writing |
| `-y` / `--yes` | `store_true` | `false` | Skip confirmation prompt |

### How Rules Combine

1. Inline CLI keywords are **merged** with config file entries.
2. If **keep** rules exist, a row must match at least one to survive.
3. If a row matches any **remove** rule, it is excluded.
4. All matching is **case-insensitive**.

### Examples

```bash
# Use config file
python clean.py --db data/kalshi_catalog.db --config filters.json

# Preview before writing
python clean.py --db data/kalshi_catalog.db --config filters.json --dry-run

# Remove specific keywords
python clean.py --db data/kalshi_catalog.db --remove "NBA,NFL,MLB"

# Keep only crypto
python clean.py --db data/kalshi_catalog.db --keep "BTC,ETH" --remove "15 min"

# Regex for tickers
python clean.py --db data/kalshi_catalog.db --ticker-remove-regex "^KX.*T$"

# Custom output path
python clean.py --db data/kalshi_catalog.db --config filters.json --output cleaned.db

# Skip confirmation
python clean.py --db data/kalshi_catalog.db --config filters.json -y
```

---

## `filters.json` — Rule Format

```jsonc
{
  "_comment": "Rules applied to contract names unless 'field' is set to 'ticker'",

  "keep": [
    "Sanctions",                                         // name LIKE %Sanctions%
    {"pattern": "tariff", "type": "regex"},              // name REGEXP 'tariff'
    {"pattern": "WAR", "field": "ticker"}                // ticker LIKE %WAR%
  ],

  "remove": [
    "NBA",                                               // name LIKE %NBA%
    {"pattern": "above \\d+\\.\\d+", "type": "regex"},   // name REGEXP pattern
    {"pattern": "^KX.*T$", "field": "ticker", "type": "regex"}  // ticker REGEXP pattern
  ]
}
```

| Entry Form | Field | Match | SQL |
|------------|-------|-------|-----|
| `"Bitcoin"` | name | LIKE | `name LIKE '%Bitcoin%'` |
| `{"pattern":"BTC"}` | name | LIKE | `name LIKE '%BTC%'` |
| `{"pattern":"\\d+" ,"type":"regex"}` | name | REGEXP | `name REGEXP '\d+'` |
| `{"pattern":"WAR","field":"ticker"}` | ticker | LIKE | `ticker LIKE '%WAR%'` |
| `{"pattern":"^KX","field":"ticker","type":"regex"}` | ticker | REGEXP | `ticker REGEXP '^KX'` |

### Shipped Filter Categories (76 patterns)

| Category | Examples |
|----------|----------|
| Mention markets | `MENTION` tickers, "What will ... say" patterns |
| Sports | NBA, NFL, MLB, NHL, UFC, F1, NASCAR, golf, esports, chess |
| Crypto | Bitcoin, Ethereum, BTC, ETH, SOL, DOGE, XRP, NFT, DeFi |
| Entertainment | Oscar, Grammy, Netflix, Spotify, album, festival |
| Weather | rain, hurricane, tornado, earthquake, temperature |
| Commodities | WTI, Brent, gold, silver, crude oil, natural gas |
| Financial | CPI, PPI, GDP, unemployment, NASDAQ, VIX, SOFR |

---

## One-Liner Recipes

```bash
# Collect last month and clean in one go
python collect.py --start 2026-05-01 --end 2026-06-01 && \
  python clean.py --db data/kalshi_catalog.db --config filters.json -y

# Test a custom filter without modifying anything
python clean.py --db data/kalshi_catalog.db --keep "sanctions,tariff,embargo" --dry-run

# Export cleaned tickers to a text file
python clean.py --db data/kalshi_catalog.db --config filters.json -y && \
  sqlite3 data/kalshi_catalog_cleaned.db "SELECT ticker FROM contracts;" > tickers.txt

# Count rows in any DB
sqlite3 data/kalshi_catalog.db "SELECT COUNT(*) FROM contracts;"
```

---

## Output Files

| File | Created by | Description |
|------|-----------|-------------|
| `data/kalshi_catalog.db` | `collect.py` | Raw contract catalog (SQLite) |
| `data/collection.log` | `collect.py` | Timestamped collection log |
| `data/collection_manifest.json` | `collect.py` | Metadata (hash, row count, duration) |
| `data/kalshi_catalog_cleaned.db` | `clean.py` | Filtered catalog (SQLite) |
