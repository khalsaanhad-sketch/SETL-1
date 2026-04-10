"""
log_engine.py — async, non-blocking flight log writer.

Public API (only these two symbols are used by app.py):
    log_entry(data: dict)          — call once per WS tick
    init_log_engine()              — call once at app startup (optional)

Behaviour:
  - Appends one CSV row per call to  logs/flight_logs.csv
  - Writes the header row automatically when the file is new or empty
  - Rotates the file when it exceeds LOG_MAX_BYTES (5 MB), preserving header
  - push failures are caught and printed; they never raise or block the caller
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
LOG_MAX_BYTES = 5 * 1024 * 1024          # 5 MB — rotate threshold

# Canonical column order — every row written in exactly this order.
# Any key in data not listed here is silently ignored so the CSV stays tidy.
CSV_COLUMNS = [
    # Position & identity
    "ts",
    "session",
    "callsign",
    "icao24",
    "lat",
    "lon",
    # Aircraft state
    "alt_ft",
    "speed_kts",
    "heading_deg",
    "vs_fpm",
    # Risk & probability
    "flight_state",
    "risk_level",
    "prob_success",
    # Decision quality
    "best_cell_prob",
    "best_cell_color",
    "best_cell_dist_nm",
    "n_green_cells",
    "n_yellow_cells",
    "n_red_cells",
    "top_option",
    # Weather
    "wx_source",
    "wx_confidence",
    "wx_ceiling_ft",
    "wx_wind_kts",
    "wx_wind_dir_deg",
    "wx_visibility_sm",
    # Terrain & data provenance
    "terrain_live",
    # System health
    "tick_ms",
    "n_runways_near",
    "crowd_ready",
    "runway_ready",
]

# ── Internal state (module-level, shared across ticks) ─────────────────────────
_lock           = asyncio.Lock()          # guards file writes + counters
_entry_count    = 0                       # entries written since last push
_last_push_ts   = time.monotonic()        # monotonic clock of last successful push
_push_in_flight = False                   # prevents overlapping push tasks


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

def _csv_row(data: dict) -> str:
    """Return a single CSV-formatted line (no newline) for the given data dict."""
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=CSV_COLUMNS,
        extrasaction="ignore",   # silently drop unknown keys
        lineterminator="",       # we add \n ourselves
    )
    writer.writerow(data)
    return buf.getvalue()


def _header_line() -> str:
    """Return the CSV header line."""
    return ",".join(CSV_COLUMNS)


def _needs_header() -> bool:
    """True if the file doesn't exist or is empty."""
    return not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0


# ── Internal async worker ──────────────────────────────────────────────────────

async def _async_log_entry(data: dict) -> None:
    global _entry_count, _last_push_ts, _push_in_flight

    async with _lock:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)

            # ── Rotation: rename if file exceeds 5 MB ──────────────────────
            if LOG_FILE.exists() and LOG_FILE.stat().st_size >= LOG_MAX_BYTES:
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                rotated = LOG_DIR / f"flight_logs_{ts}.csv"
                LOG_FILE.rename(rotated)
                print(f"[log_engine] Rotated → {rotated.name}")

            # ── Append header (if new/empty) + data row ────────────────────
            with LOG_FILE.open("a", encoding="utf-8", newline="") as fh:
                if _needs_header():
                    fh.write(_header_line() + "\n")
                fh.write(_csv_row(data) + "\n")

            _entry_count += 1

        except Exception as exc:
            print(f"[log_engine] write error: {exc}")
            return

    # ── Decide whether to push (outside the write lock) ────────────────────
    elapsed = time.monotonic() - _last_push_ts
    should_push = (_entry_count >= 20) or (elapsed >= 300)

    if should_push and not _push_in_flight:
        _push_in_flight = True
        asyncio.create_task(_push_logs_to_github())


async def _push_logs_to_github() -> None:
    """
    Background task: commit and push the logs/ directory to GitHub.

    Uses asyncio.create_subprocess_exec so the event loop is never blocked.
    All exceptions are caught; a failure here has zero effect on the WS tick.
    """
    global _entry_count, _last_push_ts, _push_in_flight

    try:
        commit_msg = (
            f"log update — "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )

        async def _run(*cmd) -> tuple[int, str]:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return proc.returncode, (stdout + stderr).decode(errors="replace").strip()

        rc, out = await _run("git", "add", "logs/")
        if rc != 0:
            print(f"[log_engine] git add failed ({rc}): {out}")
            return

        rc, out = await _run("git", "commit", "-m", commit_msg, "--allow-empty")
        if rc != 0:
            print(f"[log_engine] git commit failed ({rc}): {out}")
            return

        rc, out = await _run("git", "push", "origin", "HEAD")
        if rc != 0:
            print(f"[log_engine] git push failed ({rc}): {out}")
            return

        _entry_count  = 0
        _last_push_ts = time.monotonic()
        print(f"[log_engine] pushed logs to GitHub ({commit_msg})")

    except Exception as exc:
        print(f"[log_engine] push exception: {exc}")

    finally:
        _push_in_flight = False


# ── Sync fallback (tests / non-async callers) ──────────────────────────────────

def _sync_append(data: dict) -> None:
    """Minimal synchronous write used when no event loop is running."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8", newline="") as fh:
            if _needs_header():
                fh.write(_header_line() + "\n")
            fh.write(_csv_row(data) + "\n")
    except Exception as exc:
        print(f"[log_engine] sync_append error: {exc}")
