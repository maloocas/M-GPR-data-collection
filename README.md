# MarketGPR

**A terminal UI for building and filtering a Kalshi prediction-market contract index — designed for geopolitical risk research.**

Launch the interactive TUI, configure your collection date range and filter rules, then watch the pipeline run in real time. No CLI flags to memorise.

```text
┌─────────────────────────┐
│    $  MarketGPR          │
│   Prediction-market     │
│        pipeline          │
│                         │
│  [◉ Collect Contracts]  │
│  [◌ Clean Contracts]    │
│                         │
│  Catalog: 1,234,567     │
│  contracts              │
└─────────────────────────┘
         │
         ▼
┌─────────────────┐   ┌─────────────────────────────────┐
│ COLLECT SCREEN  │   │ CLEAN SCREEN                    │
│                 │   │                                 │
│ Start date      │   │ Input DB          Preview/Exec  │
│ End date        │   │ Config file       buttons       │
│ Enrich toggle   │   │ Keep keywords                  │
│ Delay           │   │ Remove keywords                │
│                 │   │ Dry-run switch                 │
│ [▶ Start] [←]  │   │ [◐ Preview][▶ Execute][←]      │
│ ████████████    │   │                                 │
│ Log output...   │   │ Log output...                   │
└─────────────────┘   └─────────────────────────────────┘
```

---

## Quick start

```bash
# 1. Install the TUI dependency
pip install textual

# 2. Launch the interactive terminal interface
python ui.py
```

That's it. The home screen shows two buttons — **Collect Contracts** and **Clean Contracts** — plus live status of any existing databases.

---

## TUI walkthrough

### Home screen

On launch you see the MarketGPR logo with two cards:

| Button | Description |
|--------|-------------|
| **◉ Collect Contracts** | Fetch contracts from Kalshi API into a SQLite catalog |
| **◌ Clean Contracts** | Filter an existing catalog by keywords and regex |

Below the buttons the status line shows your current databases:

```
Catalog: 1,234,567 contracts  ·  Cleaned: 45,890 contracts
```

If a database hasn't been created yet it shows `not yet available`.

### Collect screen

Opens when you press **Collect Contracts**. The form is pre-filled with sensible defaults:

| Field | Default | Description |
|-------|---------|-------------|
| Start date | 7 days ago | Begin of collection window (YYYY-MM-DD) |
| End date | today | End of collection window (YYYY-MM-DD) |
| Output path | `data/kalshi_catalog.db` | Where to write the SQLite database |
| Enrich event titles | OFF | Fetch human-readable event titles from the Kalshi `/events` endpoint |
| Delay (s/page) | 0.1 | Pause between API pages (raise if hitting rate limits) |

Hit **▶ Start Collection** to begin. The TUI runs collection in a background thread so the interface stays responsive. You'll see:

1. A progress bar advancing through the three phases
2. Live log output showing every API page, row counts, and timing

**Pipeline phases:**

| Phase | Source | Covers |
|-------|--------|--------|
| **Live** | `GET /markets` | Contracts closing in the ~90-day live window |
| **Historical** | `GET /historical/markets` | Archived (settled) contracts |
| **Enrich** (optional) | `GET /events` | Replaces ticker placeholders with event titles |

Collection is idempotent — `INSERT OR IGNORE` means you can cancel and re-run safely. The `← Back` button is disabled during a run and re-enabled on completion.

### Clean screen

Opens when you press **Clean Contracts**. Filters an existing catalog database by keywords and regex patterns, writing only the matching rows to a new database (the original is never modified).

| Field | Default | Description |
|-------|---------|-------------|
| Input database | `data/kalshi_catalog.db` | Source DB (auto-detected if it exists) |
| Config file | `filters.json` | Curated exclusion rules (76 patterns) |
| Keep keywords | *(empty)* | Comma-separated terms — narrow to rows matching ANY of these |
| Remove keywords | *(empty)* | Comma-separated terms — drop rows matching ANY of these |
| Dry-run only | OFF | Preview row counts and samples without writing |
| Output path | *(auto)* | Defaults to `<input>_cleaned.db` in the same directory |

Two action buttons:

| Button | Action |
|--------|--------|
| **◐ Preview** | Runs the filter in dry-run mode — shows matched/removed row counts and sample rows |
| **▶ Execute** | Writes the filtered database (equivalent to preview + confirm) |

The log panel shows each filter rule as it's applied:

```
✓ keep "election"  → name LIKE '%election%'
✓ keep "war"       → name LIKE '%war%'
✗ remove "NBA"     → name LIKE '%NBA%'
✗ remove "NFL"     → name LIKE '%NFL%'
```

Settings from the config file and the inline keyword fields are merged — CLI-style flags from the original scripts work identically here.

### Navigation

- Use `← Back` buttons to return to the home screen
- `Tab` / `Shift+Tab` moves focus between form fields
- Buttons are disabled during running operations
- The header clock shows current UTC time

---

## Built-in filter categories

`filters.json` ships with 76 exclusion patterns across 7 categories. These are applied when you select the config file on the Clean screen (it's pre-filled by default).

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

The config file (`filters.json`) uses this structure:

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

All matching is case-insensitive. You can also pass keywords directly in the TUI form fields — they're appended to whatever the config file provides.

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

## CLI fallback

The underlying Python modules also work as standalone scripts if you prefer the command line:

```bash
python collect.py --start 2026-01-01                # collect from the API
python clean.py --db data/kalshi_catalog.db --config filters.json  # filter
python clean.py --db data/kalshi_catalog.db --keep "war,election" --dry-run
```

See `python collect.py --help` or `python clean.py --help` for all CLI flags.

---

## File structure

```
M-GPR-data-collection/
├── ui.py                    # TUI entry-point (python ui.py)
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

## Safety guarantees

- **Input is read-only** — cleaner opens source DB via `file:path?mode=ro`
- **Output is always a new file** — defaults to `data/`, never overwrites the original
- **Idempotent** — `INSERT OR IGNORE` means partial collections resume cleanly
- **Preview before write** — the Clean screen's **Preview** button shows exact row counts before committing
- **Thread-isolated** — background workers don't block the UI

---

## Requirements

- Python 3.8+
- `textual` — `pip install textual` (required for the TUI; the CLI scripts remain zero-dependency)

Scale estimates for collection (background reference):

| Range | Est. rows | Est. DB size | Est. time | Est. traffic |
|-------|-----------|-------------|-----------|-------------|
| 1 day | ~40,000 | ~13 MB | ~40 s | ~50 MB |
| 1 month | ~1.2M | ~400 MB | ~20 min | ~350 MB |
| 1 year | ~15M | ~4.5 GB | ~4 hours | ~7.5 GB |
| 2 years | ~30M | ~9 GB | ~8 hours | ~15 GB |

MVE (multivariate event) combo markets are excluded by default — they account for ~89% of raw API volume.
