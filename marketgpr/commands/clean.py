"""MarketGPR Cleaner — Filter Kalshi contract databases by keywords and regex.

Reads a SQLite database of Kalshi contracts, applies keep/remove filter rules
against the name and ticker columns, and writes a cleaned copy to a new file.
Never modifies the original database.
"""

import argparse
import json
import os
import sys
from typing import List, Optional, Tuple, Union

from marketgpr.db import (CREATE_CONTRACTS, CREATE_EXPIRY_IDX, DATA_DIR,
                          accent, dim, header, highlight, info, ok, warn,
                          connect_readonly)


class FilterRule:
    """A single filter rule: keep or remove, matching against a column."""

    __slots__ = ("pattern", "field", "match_type")

    def __init__(self, pattern: str, field: str = "name",
                 match_type: str = "like"):
        self.pattern    = pattern
        self.field      = field
        self.match_type = match_type

    def to_sql(self) -> Tuple[str, list]:
        """Return (SQL fragment, parameter list) for this rule."""
        if self.match_type == "regex":
            return (f"{self.field} REGEXP ?", [self.pattern])
        else:
            return (f"{self.field} LIKE ?", [f"%{self.pattern}%"])

    def describe(self) -> str:
        """Human-readable summary of this rule."""
        op = "~" if self.match_type == "regex" else "LIKE"
        return f"{self.field} {op} {self.pattern!r}"


def load_json_config(path: str) -> dict:
    """Load filter rules from a JSON config file."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"Config root must be a JSON object, got {type(data).__name__}"
        )
    return data


def parse_filter_entries(entries: List[Union[str, dict]]) -> List[FilterRule]:
    """Convert a list of string/dict entries into FilterRule objects.

    String entries become name-LIKE rules.
    Dict entries must have ``pattern``; optionally ``field`` (default "name")
    and ``type`` (default "like").
    """
    rules: List[FilterRule] = []
    for entry in entries:
        if isinstance(entry, str):
            rules.append(FilterRule(pattern=entry))
        elif isinstance(entry, dict):
            rules.append(FilterRule(
                pattern=entry["pattern"],
                field=entry.get("field", "name"),
                match_type=entry.get("type", "like"),
            ))
        else:
            raise ValueError(
                f"Filter entry must be a string or object, got "
                f"{type(entry).__name__}: {entry!r}"
            )
    return rules


def parse_cli_keywords(
    value: Optional[str],
    field: str = "name",
    match_type: str = "like",
) -> List[FilterRule]:
    """Parse comma-separated CLI keyword string into FilterRule list."""
    if not value:
        return []
    return [
        FilterRule(pattern=kw.strip(), field=field, match_type=match_type)
        for kw in value.split(",")
        if kw.strip()
    ]


def build_where_clause(rules: List[FilterRule]) -> Tuple[str, list]:
    """Build a WHERE clause that OR's together all provided rules.

    Returns (sql_fragment, parameters).
    If rules is empty, returns ("1=0", []) — matches nothing.
    """
    if not rules:
        return ("1=0", [])

    clauses: List[str] = []
    params: list = []
    for rule in rules:
        fragment, rule_params = rule.to_sql()
        clauses.append(f"({fragment})")
        params.extend(rule_params)

    return ("(" + " OR ".join(clauses) + ")", params)


def filter_database(
    input_db: str,
    output_db: str,
    keep_rules: List[FilterRule],
    remove_rules: List[FilterRule],
    dry_run: bool = False,
) -> dict:
    """Apply keep/remove rules and write a cleaned database.

    Returns a dict of stats: original, keep_pass, remove_drop, final.
    """
    conn = connect_readonly(input_db)

    original = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]

    keep_where, keep_params = build_where_clause(keep_rules)
    remove_where, remove_params = build_where_clause(remove_rules)

    sql = "SELECT rowid, ticker, name, event_ticker, expiry_date, fetched_at FROM contracts"
    conditions: List[str] = []
    all_params: list = []

    if keep_rules:
        conditions.append(keep_where)
        all_params.extend(keep_params)
    if remove_rules:
        conditions.append(f"NOT {remove_where}")
        all_params.extend(remove_params)

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    if dry_run:
        count_sql = sql.replace(
            "SELECT rowid, ticker, name, event_ticker, expiry_date, fetched_at",
            "SELECT COUNT(*)",
        )
        final_count = conn.execute(count_sql, all_params).fetchone()[0]

        keep_pass = original
        if keep_rules:
            keep_sql = "SELECT COUNT(*) FROM contracts WHERE " + keep_where
            keep_pass = conn.execute(keep_sql, keep_params).fetchone()[0]

        remove_count = 0
        if remove_rules:
            base = "SELECT COUNT(*) FROM contracts"
            if keep_rules:
                base += " WHERE " + keep_where
            remove_where_sql = (
                " WHERE " + remove_where
                if not keep_rules
                else " AND " + remove_where
            )
            remove_count = conn.execute(
                base + remove_where_sql,
                keep_params + remove_params if keep_rules else remove_params,
            ).fetchone()[0]

        conn.close()

        p = lambda s: print(s, flush=True)
        p("")
        p(header("=== DRY RUN ==="))
        p(accent(f"Original rows:              {original:>10,}"))
        if keep_rules:
            p(accent(f"After keep filters:         {keep_pass:>10,}  ({keep_pass/original*100:.1f}%)"))
        if remove_rules:
            p(warn(f"Rows matched by remove:     {remove_count:>10,}"))
        p(ok(f"Final rows:                 {final_count:>10,}  ({final_count/original*100:.1f}%)"))
        p(warn(f"Rows removed:               {original - final_count:>10,}"))

        _print_samples(input_db, conditions, all_params)
        _print_removed_samples(input_db, keep_rules, remove_rules,
                               keep_params, remove_params)

        return {
            "original":    original,
            "keep_pass":   keep_pass if keep_rules else original,
            "remove_drop": remove_count,
            "final":       final_count,
        }

    count_sql = sql.replace(
        "SELECT rowid, ticker, name, event_ticker, expiry_date, fetched_at",
        "SELECT COUNT(*)",
    )
    final_count = conn.execute(count_sql, all_params).fetchone()[0]

    keep_pass = original
    if keep_rules:
        keep_sql = "SELECT COUNT(*) FROM contracts WHERE " + keep_where
        keep_pass = conn.execute(keep_sql, keep_params).fetchone()[0]

    remove_count = 0
    if remove_rules:
        base = "SELECT COUNT(*) FROM contracts"
        if keep_rules:
            base += " WHERE " + keep_where
        remove_where_sql = (
            " WHERE " + remove_where
            if not keep_rules
            else " AND " + remove_where
        )
        remove_count = conn.execute(
            base + remove_where_sql,
            keep_params + remove_params if keep_rules else remove_params,
        ).fetchone()[0]

    if os.path.exists(output_db):
        os.remove(output_db)

    import sqlite3
    out_conn = sqlite3.connect(output_db)
    out_conn.execute("PRAGMA journal_mode=WAL")
    out_conn.execute("PRAGMA synchronous=NORMAL")
    out_conn.execute(CREATE_CONTRACTS)
    out_conn.execute(CREATE_EXPIRY_IDX)

    rows = conn.execute(sql, all_params).fetchall()
    out_conn.executemany(
        "INSERT INTO contracts(ticker, name, event_ticker, expiry_date, fetched_at) VALUES (?,?,?,?,?)",
        [(r[1], r[2], r[3], r[4], r[5]) for r in rows],
    )
    out_conn.commit()
    out_conn.close()
    conn.close()

    p = lambda s: print(s, flush=True)
    p("")
    p(accent(f"Original:        {original:>10,} rows"))
    if keep_rules:
        p(info(f"After keep:       {keep_pass:>10,}  ({keep_pass/original*100:.1f}%)"))
    if remove_rules:
        p(warn(f"Matched rm:       {remove_count:>10,}"))
    p(ok(f"Final:           {final_count:>10,}  ({final_count/original*100:.1f}%)"))
    p(warn(f"Removed:          {original - final_count:>10,}"))
    p("")
    p(ok(f"Wrote: {output_db}"))

    return {
        "original":    original,
        "keep_pass":   keep_pass,
        "remove_drop": remove_count,
        "final":       final_count,
    }


def _print_samples(input_db: str, conditions: List[str], params: list) -> None:
    """Print sample rows that pass all filters."""
    p = lambda s: print(s, flush=True)
    conn = connect_readonly(input_db)

    sql = "SELECT ticker, name FROM contracts"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    rows = conn.execute(sql + " LIMIT 10", params).fetchall()
    conn.close()

    if rows:
        p("")
        p(ok("--- Sample rows that WOULD be kept ---"))
        for ticker, name in rows:
            p(dim(f"  {ticker:<50}  ") + name)


def _print_removed_samples(
    input_db: str,
    keep_rules: List[FilterRule],
    remove_rules: List[FilterRule],
    keep_params: list,
    remove_params: list,
) -> None:
    """Print sample rows that would be removed."""
    if not remove_rules:
        return

    p = lambda s: print(s, flush=True)
    conn = connect_readonly(input_db)

    remove_where, _ = build_where_clause(remove_rules)
    base = "SELECT ticker, name FROM contracts"
    all_params: list = []
    if keep_rules:
        keep_where, _ = build_where_clause(keep_rules)
        base += " WHERE " + keep_where
        all_params.extend(keep_params)
        base += " AND " + remove_where
        all_params.extend(remove_params)
    else:
        base += " WHERE " + remove_where
        all_params = remove_params

    rows = conn.execute(base + " LIMIT 10", all_params).fetchall()
    conn.close()

    if rows:
        p("")
        p(warn("--- Sample rows that WOULD be removed ---"))
        for ticker, name in rows:
            p(dim(f"  {ticker:<50}  ") + name)


def resolve_output_path(input_db: str, output_arg: Optional[str]) -> str:
    """Determine the output database path.  Defaults to data/<input>_cleaned.db."""
    if output_arg:
        return output_arg

    base = os.path.splitext(os.path.basename(input_db))[0]
    return os.path.join(DATA_DIR, f"{base}_cleaned.db")


def register_args(parser: argparse.ArgumentParser):
    parser.add_argument("--db", required=True, help="Path to input SQLite database")
    parser.add_argument("--config", help="Path to JSON config file with filter rules (keep/remove arrays)")
    parser.add_argument("--output", help="Path to output SQLite database (default: data/<input>_cleaned.db)")
    parser.add_argument("--keep",          help="Comma-separated keywords to KEEP (name LIKE)")
    parser.add_argument("--remove",        help="Comma-separated keywords to REMOVE (name LIKE)")
    parser.add_argument("--keep-regex",    help="Comma-separated regex patterns to KEEP (name REGEXP)")
    parser.add_argument("--remove-regex",  help="Comma-separated regex patterns to REMOVE (name REGEXP)")
    parser.add_argument("--ticker-keep",         help="Comma-separated keywords to KEEP (ticker LIKE)")
    parser.add_argument("--ticker-remove",       help="Comma-separated keywords to REMOVE (ticker LIKE)")
    parser.add_argument("--ticker-keep-regex",   help="Comma-separated regex patterns to KEEP (ticker REGEXP)")
    parser.add_argument("--ticker-remove-regex", help="Comma-separated regex patterns to REMOVE (ticker REGEXP)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, do not write output")
    parser.add_argument("-y", "--yes",  action="store_true", help="Skip confirmation prompt")


def run(args: argparse.Namespace):
    p = lambda s: print(s, flush=True)

    if not os.path.isfile(args.db):
        p(warn(f"Error: database not found: {args.db}"))
        sys.exit(1)

    if args.config and not os.path.isfile(args.config):
        p(warn(f"Error: config file not found: {args.config}"))
        sys.exit(1)

    keep_rules: List[FilterRule] = []
    remove_rules: List[FilterRule] = []

    if args.config:
        config = load_json_config(args.config)
        keep_rules.extend(parse_filter_entries(config.get("keep", [])))
        remove_rules.extend(parse_filter_entries(config.get("remove", [])))

    keep_rules.extend(parse_cli_keywords(args.keep, "name", "like"))
    remove_rules.extend(parse_cli_keywords(args.remove, "name", "like"))
    keep_rules.extend(parse_cli_keywords(args.keep_regex, "name", "regex"))
    remove_rules.extend(parse_cli_keywords(args.remove_regex, "name", "regex"))
    keep_rules.extend(parse_cli_keywords(args.ticker_keep, "ticker", "like"))
    remove_rules.extend(parse_cli_keywords(args.ticker_remove, "ticker", "like"))
    keep_rules.extend(parse_cli_keywords(args.ticker_keep_regex, "ticker", "regex"))
    remove_rules.extend(parse_cli_keywords(args.ticker_remove_regex, "ticker", "regex"))

    if not keep_rules and not remove_rules:
        p(warn("Error: no filter rules provided. Use --config, --keep, or --remove."))
        sys.exit(1)

    output_db = resolve_output_path(args.db, args.output)

    p("")
    p(header("=== MarketGPR  ·  Contract Cleaner ==="))

    if keep_rules:
        p("")
        p(ok(f"Keep rules ({len(keep_rules)}):"))
        for r in keep_rules:
            p(f"  {ok('✓')} " + accent(r.describe()))
    if remove_rules:
        p("")
        p(warn(f"Remove rules ({len(remove_rules)}):"))
        for r in remove_rules:
            p(f"  {warn('✗')} " + dim(r.describe()))

    p("")
    p(accent(f"Input:  {args.db}"))
    p(accent(f"Output: {output_db}"))

    if not args.dry_run and not args.yes:
        try:
            response = input(f"\n{highlight('Proceed?')} [y/N] ").strip().lower()
            if response not in ("y", "yes"):
                p(warn("Aborted."))
                sys.exit(0)
        except (KeyboardInterrupt, EOFError):
            p("")
            p(warn("Aborted."))
            sys.exit(0)

    filter_database(
        input_db=args.db,
        output_db=output_db,
        keep_rules=keep_rules,
        remove_rules=remove_rules,
        dry_run=args.dry_run,
    )
