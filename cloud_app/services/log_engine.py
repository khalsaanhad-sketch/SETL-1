"""
log_engine.py — async, non-blocking flight log writer.

Public API (only these two symbols are used by app.py):
    log_entry(data: dict)   — call once per WS tick
    init_log_engine()       — call once at app startup (optional)

Behaviour:
  - Appends one CSV row per call to  logs/flight_logs.csv
  - Writes the header row automatically when starting a new file
  - Rotates the file when it exceeds LOG_MAX_BYTES (5 MB); rotated files
    are named  logs/flight_logs_<UTC-timestamp>.csv
  - logs/ is in .gitignore — these files are NEVER committed to git
"""

import asyncio
import csv
import io
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────────
LOG_DIR       = Path("logs")
LOG_FILE      = LOG_DIR / "flight_logs.csv"
LOG_MAX_BYTES = 5 * 1024 * 1024        # 5 MB — rotate threshold

# Ordered column list — matches the dict produced by app.py's log_entry() call.
# Add new fields here (append to end) to keep existing files readable.
CSV_COLUMNS = [
    "ts", "session", "callsign", "icao24", "lat", "lon",
    "alt_ft", "speed_kts", "heading_deg", "vs_fpm",
    "flight_state", "risk_level", "prob_success",
    "best_cell_prob", "best_cell_color", "best_cell_dist_nm",
    "n_green_cells", "n_yellow_cells", "n_red_cells", "top_option",
    "wx_source", "wx_confidence", "wx_ceiling_ft",
    "wx_wind_kts", "wx_wind_dir_deg", "wx_visibility_sm",
    "terrain_live", "tick_ms", "n_runways_near", "crowd_ready", "runway_ready",
]

# ── Internal state ─────────────────────────────────────────────────────────────
_lock = asyncio.Lock()


# ── Public helpers ─────────────────────────────────────────────────────────────

def init_log_engine() -> None:
    """Create the logs/ directory if absent. Safe to call multiple times."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def log_entry(data: dict) -> None:
    """
    Non-blocking entry point called from the WS tick.

    Schedules _async_log_entry() as a background asyncio task so the
    WS tick returns immediately without waiting for any I/O.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_async_log_entry(data))
        else:
            _sync_append(data)
    except Exception as exc:
        print(f"[log_engine] log_entry error: {exc}")


# ── Internal helpers ───────────────────────────────────────────────────────────

def _row(data: dict) -> list:
    """Return a list of values in CSV_COLUMNS order; missing keys → empty string."""
    return [data.get(col, "") for col in CSV_COLUMNS]


def _write_header(fh) -> None:
    writer = csv.writer(fh)
    writer.writerow(CSV_COLUMNS)


def _write_row(fh, data: dict) -> None:
    writer = csv.writer(fh)
    writer.writerow(_row(data))


# ── Internal async worker ──────────────────────────────────────────────────────

async def _async_log_entry(data: dict) -> None:
    async with _lock:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)

            # ── Rotation: rename current file if it exceeds 5 MB ───────────
            if LOG_FILE.exists() and LOG_FILE.stat().st_size >= LOG_MAX_BYTES:
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                rotated = LOG_DIR / f"flight_logs_{ts}.csv"
                LOG_FILE.rename(rotated)
                print(f"[log_engine] Rotated → {rotated.name}")

            # ── Write header if this is a brand-new file ────────────────────
            new_file = not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0
            with LOG_FILE.open("a", encoding="utf-8", newline="") as fh:
                if new_file:
                    _write_header(fh)
                _write_row(fh, data)

        except Exception as exc:
            print(f"[log_engine] write error: {exc}")


# ── Sync fallback (non-async callers) ─────────────────────────────────────────

def _sync_append(data: dict) -> None:
    """Minimal synchronous write used when no event loop is running."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        new_file = not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0
        with LOG_FILE.open("a", encoding="utf-8", newline="") as fh:
            if new_file:
                _write_header(fh)
            _write_row(fh, data)
    except Exception as exc:
        print(f"[log_engine] sync_append error: {exc}")
