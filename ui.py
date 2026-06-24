#!/usr/bin/env python3
"""MarketGPR TUI — interactive terminal interface for contract collection & filtering.

Usage:
    python ui.py

Dependencies: textual (pip install textual)
"""

from __future__ import annotations

import io
import logging
import os
import re as _re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Static,
    Switch,
)

# ── Project imports ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import DATA_DIR, init_db, write_manifest  # noqa: E402
import collect  # noqa: E402
import clean    # noqa: E402

# ── Message queue (thread-safe cross-thread communication) ──────────────────
_ui_queue: Queue = Queue()

# ── ANSI strip helper ──────────────────────────────────────────────────────
_ANSI_PAT = _re.compile(r"\x1b\[[0-9;]*m")


def _plain(s: str) -> str:
    return _ANSI_PAT.sub("", s)


# ── Composable stdout / log capture ────────────────────────────────────────

class _TeeWriter(io.TextIOBase):
    """Writes to the original stream *and* pushes lines into the UI queue."""

    def __init__(self, original):
        self._orig = original

    def write(self, text: str) -> int:
        if text and text.strip():
            _ui_queue.put(("print", _plain(text.rstrip())))
        return self._orig.write(text)

    def flush(self):
        self._orig.flush()


class _UILogHandler(logging.Handler):
    """Sends every log record into the UI queue."""

    def emit(self, record: logging.LogRecord):
        _ui_queue.put(("log", self.format(record)))


# ── Colour palette ─────────────────────────────────────────────────────────
NAVY       = "#0a1628"
DEEP_BG    = "#060f1e"
DARK_PANEL = "#0d1f3c"
MID_BLUE   = "#1a3a6b"
ACCENT     = "#4a9eff"
LIGHT_BLUE = "#8ec8ff"
TEXT       = "#c8dfff"
TEXT_DIM   = "#7a8faa"
GREEN_OK   = "#2ecc71"
YELLOW_W   = "#f1c40f"
RED_ERR    = "#e74c3c"
WHITE      = "#ffffff"


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN SCREEN
# ═══════════════════════════════════════════════════════════════════════════

class MainScreen(Screen):
    """Landing screen — choose a tool."""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Container(
            Vertical(
                Static("[bold]$[/]  Market[bold]GPR[/]", id="logo"),
                Static("Prediction-market pipeline", id="tagline"),
                Horizontal(
                    Button("  ◉  Collect Contracts", id="btn-collect", variant="primary"),
                    Button("  ◌  Clean Contracts",   id="btn-clean",   variant="default"),
                    id="home-buttons",
                ),
                Static("", id="home-status"),
                id="home-center",
            ),
            id="home-container",
        )
        yield Footer()

    def on_mount(self):
        self._update_status()

    def _update_status(self):
        db_path      = os.path.join(DATA_DIR, "kalshi_catalog.db")
        cleaned_path = os.path.join(DATA_DIR, "kalshi_catalog_cleaned.db")
        parts: list[str] = []

        for label, path, color in [
            ("Catalog", db_path, GREEN_OK),
            ("Cleaned", cleaned_path, ACCENT),
        ]:
            if os.path.isfile(path):
                try:
                    import sqlite3
                    conn = sqlite3.connect(path)
                    cnt  = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
                    conn.close()
                    parts.append(f"{label}: [bold {color}]{cnt:,}[/] contracts")
                except Exception:
                    parts.append(f"{label}: [bold {RED_ERR}]error reading[/]")
            else:
                parts.append(f"{label}: [dim]not yet available[/]")

        self.query_one("#home-status", Static).update("  ·  ".join(parts))

    def on_screen_resume(self):
        self._update_status()
        self.app.sub_title = "Prediction-market pipeline"

    @on(Button.Pressed, "#btn-collect")
    def go_collect(self):
        self.app.push_screen(CollectScreen())

    @on(Button.Pressed, "#btn-clean")
    def go_clean(self):
        self.app.push_screen(CleanScreen())


# ═══════════════════════════════════════════════════════════════════════════
#  COLLECT SCREEN
# ═══════════════════════════════════════════════════════════════════════════

class CollectScreen(Screen):
    """Configure and run a catalogue collection."""

    running: bool = False

    def compose(self) -> ComposeResult:
        today      = datetime.now(timezone.utc)
        week_ago   = today.replace(day=today.day - 7).strftime("%Y-%m-%d")
        today_str  = today.strftime("%Y-%m-%d")
        db_default = os.path.join(DATA_DIR, "kalshi_catalog.db")

        yield Header(show_clock=True)
        yield Container(
            Vertical(
                Label("Contract Collection", id="screen-title"),
                Horizontal(
                    Vertical(
                        Label("Start date  (YYYY-MM-DD)"),
                        Input(value=week_ago, id="co-start", placeholder="e.g. 2026-01-01"),
                        Label("End date  (YYYY-MM-DD)"),
                        Input(value=today_str, id="co-end", placeholder="e.g. 2026-06-23"),
                        Label("Output path"),
                        Input(value=db_default, id="co-db", placeholder="data/kalshi_catalog.db"),
                        Horizontal(
                            Switch(value=False, id="co-enrich"),
                            Label("Enrich event titles"),
                            id="switch-row",
                        ),
                        id="co-left",
                    ),
                    Vertical(
                        Label("Delay  (s / page)"),
                        Input(value="0.1", id="co-delay", placeholder="0.1"),
                        Label(""),
                        Label(""),
                        Horizontal(
                            Button("  ▶  Start Collection", id="co-run", variant="primary"),
                            Button("  ←  Back", id="co-back", variant="default"),
                        ),
                        id="co-right",
                    ),
                    id="co-form",
                ),
                ProgressBar(total=100, show_eta=False, id="co-progress"),
                RichLog(highlight=True, markup=True, max_lines=200, id="co-log"),
                id="co-body",
            ),
            id="co-container",
        )
        yield Footer()

    def on_mount(self):
        self.query_one("#co-progress", ProgressBar).display = False

    def on_screen_resume(self):
        self.app.sub_title = "Collect"

    @on(Button.Pressed, "#co-back")
    def on_back(self):
        if not self.running:
            self.dismiss()

    @on(Button.Pressed, "#co-run")
    def on_run(self):
        if self.running:
            return
        self.running = True
        self.query_one("#co-run", Button).disabled  = True
        self.query_one("#co-back", Button).disabled = True
        self.query_one("#co-run", Button).label     = "  ⋯  Running ..."

        log = self.query_one("#co-log", RichLog)
        log.clear()
        log.write("[bold #4a9eff]Starting collection ...[/]")

        progress = self.query_one("#co-progress", ProgressBar)
        progress.display = True
        progress.update(total=100, progress=0)

        start = self.query_one("#co-start", Input).value.strip()
        end   = self.query_one("#co-end",   Input).value.strip()
        db    = self.query_one("#co-db",    Input).value.strip()
        delay = self.query_one("#co-delay", Input).value.strip()
        enrich = self.query_one("#co-enrich", Switch).value

        self._run_collection(start, end, db, delay, enrich)
        self.set_timer(0.3, self._poll_log)

    def _poll_log(self):
        """Drain queue — called on the main thread via timer."""
        log      = self.query_one("#co-log", RichLog)
        progress = self.query_one("#co-progress", ProgressBar)
        count = 0
        while count < 50:
            try:
                kind, msg = _ui_queue.get_nowait()
            except Empty:
                break
            count += 1
            if kind in ("log", "print"):
                log.write(msg)
            elif kind == "progress":
                try:
                    progress.update(progress=min(float(msg), 100))
                except (ValueError, TypeError):
                    pass
            elif kind == "done":
                progress.display = False
                self.running = False
                self.query_one("#co-run", Button).disabled  = False
                self.query_one("#co-back", Button).disabled = False
                self.query_one("#co-run", Button).label     = "  ▶  Start Collection"
                if msg:
                    log.write(f"\n[bold #2ecc71]✓ {msg}[/]")
                else:
                    log.write("\n[bold #e74c3c]✗ Collection failed[/]")
            elif kind == "error":
                log.write(f"[bold #e74c3c]{msg}[/]")

        if self.running:
            self.set_timer(0.3, self._poll_log)

    @work(thread=True)
    def _run_collection(self, start_val: str, end_val: str, db_val: str,
                        delay_val: str, enrich: bool):
        """Worker thread — runs the full collection pipeline."""
        try:
            start_ts = collect.parse_date(start_val)
            end_ts   = collect.parse_date(end_val)
        except Exception as e:
            _ui_queue.put(("error", f"Date parse error: {e}"))
            _ui_queue.put(("done", ""))
            return

        try:
            delay = float(delay_val)
        except ValueError:
            _ui_queue.put(("error", f"Invalid delay: {delay_val!r}"))
            _ui_queue.put(("done", ""))
            return

        # Swap in the UI log handler + tee stdout
        old_stream_handlers = [h for h in collect.log.handlers
                               if isinstance(h, logging.StreamHandler)]
        for h in old_stream_handlers:
            collect.log.removeHandler(h)

        ui_h = _UILogHandler()
        ui_h.setLevel(logging.INFO)
        ui_h.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s"))
        collect.log.addHandler(ui_h)
        collect.log.setLevel(logging.INFO)

        old_stdout = sys.stdout
        sys.stdout = _TeeWriter(old_stdout)

        try:
            started        = time.time()
            conn           = init_db(db_val)
            existing       = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
            _ui_queue.put(("print", f"Existing rows in DB: {existing:,}"))

            cutoff     = collect.get_cutoff()
            fetched_at = datetime.now(timezone.utc).isoformat()
            inserted   = 0

            if end_ts >= cutoff:
                live_start = max(start_ts, int(cutoff))
                _ui_queue.put(("print",
                    f"\n>>> Phase 1: LIVE  "
                    f"{datetime.fromtimestamp(live_start, tz=timezone.utc).strftime('%Y-%m-%d')}"
                    f" → {datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime('%Y-%m-%d')}"))
                n = collect.collect_live(conn, live_start, end_ts, fetched_at, delay)
                inserted += n
                _ui_queue.put(("progress", "33"))

            if start_ts < cutoff:
                _ui_queue.put(("print",
                    f"\n>>> Phase 2: HIST  "
                    f"{datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime('%Y-%m-%d')}"
                    f" → cutoff"))
                n = collect.collect_historical(conn, start_ts, fetched_at, delay)
                inserted += n
                _ui_queue.put(("progress", "66"))

            if enrich and inserted:
                _ui_queue.put(("print", "\n>>> Phase 3: ENRICH event titles"))
                collect.enrich_titles(conn, delay)

            row_count = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
            conn.close()
            duration = time.time() - started

            write_manifest(db_val, start_ts, end_ts, duration, collect.PROD_BASE)
            _ui_queue.put(("progress", "100"))
            _ui_queue.put(("done",
                f"{row_count:,} total rows  |  {inserted:,} inserted  |  {duration:.1f}s"))

        except Exception:
            _ui_queue.put(("error", traceback.format_exc()))
            _ui_queue.put(("done", ""))
        finally:
            sys.stdout = old_stdout
            collect.log.removeHandler(ui_h)
            for h in old_stream_handlers:
                collect.log.addHandler(h)


# ═══════════════════════════════════════════════════════════════════════════
#  CLEAN SCREEN
# ═══════════════════════════════════════════════════════════════════════════

class CleanScreen(Screen):
    """Configure and run a cleaning job."""

    running: bool = False

    def compose(self) -> ComposeResult:
        db_default = os.path.join(DATA_DIR, "kalshi_catalog.db")
        if not os.path.isfile(db_default):
            db_default = ""

        yield Header(show_clock=True)
        yield Container(
            Vertical(
                Label("Contract Cleaning", id="screen-title"),
                Horizontal(
                    Vertical(
                        Label("Input database"),
                        Input(value=db_default, id="cl-db", placeholder="data/kalshi_catalog.db"),
                        Label("Config file  (optional)"),
                        Input(value="filters.json", id="cl-config", placeholder="filters.json"),
                        Label("Keep keywords  (comma-separated)"),
                        Input(value="", id="cl-keep", placeholder="election, war, conflict"),
                        Label("Remove keywords  (comma-separated)"),
                        Input(value="", id="cl-remove", placeholder="NBA, NFL, crypto"),
                        Horizontal(
                            Switch(value=False, id="cl-dryrun"),
                            Label("Dry-run only  (preview before writing)"),
                            id="switch-row",
                        ),
                        id="cl-left",
                    ),
                    Vertical(
                        Label("Output path  (optional)"),
                        Input(value="", id="cl-output", placeholder="data/kalshi_catalog_cleaned.db"),
                        Label(""),
                        Label(""),
                        Label(""),
                        Label(""),
                        Horizontal(
                            Button("  ◐  Preview",  id="cl-preview", variant="primary"),
                            Button("  ▶  Execute",   id="cl-execute", variant="warning"),
                            Button("  ←  Back",      id="cl-back",    variant="default"),
                        ),
                        id="cl-right",
                    ),
                    id="cl-form",
                ),
                RichLog(highlight=True, markup=True, max_lines=200, id="cl-log"),
                id="cl-body",
            ),
            id="cl-container",
        )
        yield Footer()

    def on_screen_resume(self):
        self.app.sub_title = "Clean"

    @on(Button.Pressed, "#cl-back")
    def on_back(self):
        if not self.running:
            self.dismiss()

    def _gather_rules(self):
        """Collect keep/remove rules from config file and inline fields."""
        db_path   = self.query_one("#cl-db",    Input).value.strip()
        config_p  = self.query_one("#cl-config", Input).value.strip()
        keep_val  = self.query_one("#cl-keep",   Input).value.strip()
        remove_val = self.query_one("#cl-remove", Input).value.strip()
        output_val = self.query_one("#cl-output", Input).value.strip()

        log = self.query_one("#cl-log", RichLog)

        if not db_path or not os.path.isfile(db_path):
            log.write(f"[bold #e74c3c]Error: database not found: {db_path or '(empty)'}[/]")
            return None

        if not config_p and not keep_val and not remove_val:
            log.write("[bold #e74c3c]Error: provide a config, keep keywords, or remove keywords[/]")
            return None

        if config_p and not os.path.isfile(config_p):
            log.write(f"[bold #e74c3c]Error: config file not found: {config_p}[/]")
            return None

        keep_rules = []
        remove_rules = []
        if config_p:
            cfg = clean.load_json_config(config_p)
            keep_rules.extend(clean.parse_filter_entries(cfg.get("keep", [])))
            remove_rules.extend(clean.parse_filter_entries(cfg.get("remove", [])))
        keep_rules.extend(clean.parse_cli_keywords(keep_val if keep_val else None, "name", "like"))
        remove_rules.extend(clean.parse_cli_keywords(remove_val if remove_val else None, "name", "like"))

        output_db = clean.resolve_output_path(db_path, output_val if output_val else None)

        return {
            "db_path": db_path,
            "output_db": output_db,
            "keep_rules": keep_rules,
            "remove_rules": remove_rules,
        }

    @on(Button.Pressed, "#cl-preview")
    def on_preview(self):
        if self.running:
            return
        self._start_clean(dry_run=True)

    @on(Button.Pressed, "#cl-execute")
    def on_execute(self):
        if self.running:
            return
        self._start_clean(dry_run=False)

    def _start_clean(self, dry_run: bool):
        params = self._gather_rules()
        if params is None:
            return

        self.running = True
        self.query_one("#cl-preview", Button).disabled = True
        self.query_one("#cl-execute", Button).disabled = True
        self.query_one("#cl-back", Button).disabled    = True

        log = self.query_one("#cl-log", RichLog)
        log.clear()
        mode = "DRY RUN" if dry_run else "EXECUTE"
        log.write(f"[bold #4a9eff]--- {mode} ---[/]")
        log.write(f"Input:  {params['db_path']}")
        log.write(f"Output: {params['output_db']}")

        for r in params["keep_rules"]:
            log.write(f"  [bold #2ecc71]✓[/] {r.describe()}")
        for r in params["remove_rules"]:
            log.write(f"  [bold #e74c3c]✗[/] {r.describe()}")

        self._run_clean(params["db_path"], params["output_db"],
                        params["keep_rules"], params["remove_rules"], dry_run)
        self.set_timer(0.3, self._poll_log)

    @work(thread=True)
    def _run_clean(self, db_path, output_db, keep_rules, remove_rules, dry_run):
        """Worker thread — runs filter_database."""
        old_stdout = sys.stdout
        sys.stdout = _TeeWriter(old_stdout)

        try:
            clean.filter_database(
                input_db=db_path,
                output_db=output_db,
                keep_rules=keep_rules,
                remove_rules=remove_rules,
                dry_run=dry_run,
            )
        except Exception:
            _ui_queue.put(("error", traceback.format_exc()))
        finally:
            sys.stdout = old_stdout
            _ui_queue.put(("done", ""))

    def _poll_log(self):
        """Drain queue on the main thread."""
        log = self.query_one("#cl-log", RichLog)
        count = 0
        while count < 50:
            try:
                kind, msg = _ui_queue.get_nowait()
            except Empty:
                break
            count += 1
            if kind in ("log", "print"):
                log.write(msg)
            elif kind == "error":
                log.write(f"[bold #e74c3c]{msg}[/]")
            elif kind == "done":
                self.running = False
                self.query_one("#cl-preview", Button).disabled = False
                self.query_one("#cl-execute", Button).disabled = False
                self.query_one("#cl-back", Button).disabled    = False

        if self.running:
            self.set_timer(0.3, self._poll_log)


# ═══════════════════════════════════════════════════════════════════════════
#  APP
# ═══════════════════════════════════════════════════════════════════════════

class MarketGPRApp(App):
    """MarketGPR — prediction-market contract pipeline."""

    TITLE = "MarketGPR"

    CSS = f"""
    * {{
        scrollbar-background: {DARK_PANEL};
        scrollbar-background-hover: {MID_BLUE};
        scrollbar-color: {ACCENT};
        scrollbar-color-hover: {LIGHT_BLUE};
    }}

    Screen {{
        background: {NAVY};
    }}

    Header {{
        background: {DEEP_BG};
        color: {ACCENT};
        border-bottom: hkey {MID_BLUE};
    }}

    Footer {{
        background: {DEEP_BG};
        color: {TEXT_DIM};
        border-top: hkey {MID_BLUE};
    }}

    /* ── Main screen ─────────────────────────────────── */

    #home-container {{
        align: center middle;
        height: 100%;
    }}

    #home-center {{
        width: 66;
        height: auto;
        align: center middle;
        padding: 2 4;
    }}

    #logo {{
        width: 100%;
        content-align: center middle;
        color: {WHITE};
        text-style: bold;
    }}

    #tagline {{
        width: 100%;
        content-align: center middle;
        color: {TEXT_DIM};
        margin-bottom: 2;
    }}

    #home-buttons {{
        width: 100%;
        align: center middle;
    }}

    #home-buttons Button {{
        margin: 0 1;
        min-width: 28;
    }}

    #home-status {{
        width: 100%;
        content-align: center middle;
        margin-top: 2;
        color: {TEXT_DIM};
    }}

    /* ── Shared screen chrome ─────────────────────────── */

    #screen-title {{
        width: 100%;
        content-align: center middle;
        text-style: bold;
        color: {WHITE};
        padding: 1 0;
        margin-bottom: 1;
    }}

    /* ── Collect / Clean containers ───────────────────── */

    #co-container, #cl-container {{
        padding: 1 2;
        height: 100%;
    }}

    #co-body, #cl-body {{
        height: 100%;
    }}

    #co-form, #cl-form {{
        width: 100%;
        height: auto;
    }}

    #co-left, #cl-left {{
        width: 3fr;
        margin-right: 2;
    }}

    #co-right, #cl-right {{
        width: 2fr;
    }}

    #switch-row {{
        margin-top: 1;
        align: left middle;
    }}

    #switch-row Label {{
        margin-left: 1;
        color: {TEXT_DIM};
    }}

    /* ── Inputs ───────────────────────────────────────── */

    Input {{
        background: {DEEP_BG};
        color: {TEXT};
        border: solid {MID_BLUE};
        margin-bottom: 1;
    }}

    Input:focus {{
        border: solid {ACCENT};
    }}

    Input > .input--placeholder {{
        color: {TEXT_DIM};
    }}

    /* ── Labels ───────────────────────────────────────── */

    Label {{
        color: {TEXT_DIM};
        text-style: bold;
        margin-top: 1;
    }}

    /* ── Buttons ──────────────────────────────────────── */

    Button {{
        margin: 0 1;
    }}

    /* ── ProgressBar ──────────────────────────────────── */

    ProgressBar {{
        margin: 1 0;
    }}

    ProgressBar > .bar--bar {{
        color: {ACCENT};
    }}

    /* ── RichLog ──────────────────────────────────────── */

    RichLog {{
        background: {DARK_PANEL};
        color: {WHITE};
        border: solid {MID_BLUE};
        margin-top: 1;
        height: 1fr;
        min-height: 8;
        padding: 0 1;
    }}

    /* ── Switch ───────────────────────────────────────── */

    Switch {{
        color: {GREEN_OK};
    }}
    """

    def on_mount(self):
        self.push_screen(MainScreen())


# ── Entry-point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    MarketGPRApp().run()
