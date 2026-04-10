"""
log_engine.py — async, non-blocking flight log writer with GitHub sync.

Public API (only these two symbols are used by app.py):
    log_entry(data: dict)          — call once per WS tick
    init_log_engine()              — call once at app startup (optional)

Behaviour:
  - Appends one JSON line per call to  logs/flight_logs.jsonl
  - Rotates the file when it exceeds LOG_MAX_BYTES (5 MB)
  - Pushes to GitHub in a background task when:
      * PUSH_EVERY_N entries have been written  (default 20), OR
      * PUSH_EVERY_SECS seconds have elapsed since last push (default 300)
  - push failures are caught and printed; they never raise or block the caller
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────────
LOG_DIR       = Path("logs")
LOG_FILE      = LOG_DIR / "flight_logs.jsonl"
LOG_MAX_BYTES = 5 * 1024 * 1024        # 5 MB — rotate threshold
PUSH_EVERY_N  = 20                     # entries between pushes
PUSH_EVERY_S  = 300                    # seconds between pushes (5 min)

# ── Internal state (module-level, shared across ticks) ─────────────────────────
_lock          = asyncio.Lock()        # guards file writes + counters
_entry_count   = 0                     # entries written since last push
_last_push_ts  = time.monotonic()      # monotonic clock of last successful push
_push_in_flight = False                # prevents overlapping push tasks


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
            # Fallback for sync contexts (e.g. tests) — run synchronously
            _sync_append(data)
    except Exception as exc:
        print(f"[log_engine] log_entry error: {exc}")


# ── Internal async worker ──────────────────────────────────────────────────────

async def _async_log_entry(data: dict) -> None:
    global _entry_count, _last_push_ts, _push_in_flight

    async with _lock:
        try:
            # Ensure directory exists (idempotent)
            LOG_DIR.mkdir(parents=True, exist_ok=True)

            # ── Rotation: rename if file exceeds 5 MB ──────────────────────
            if LOG_FILE.exists() and LOG_FILE.stat().st_size >= LOG_MAX_BYTES:
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                rotated = LOG_DIR / f"log_{ts}.jsonl"
                LOG_FILE.rename(rotated)
                print(f"[log_engine] Rotated → {rotated.name}")

            # ── Append one JSON line ────────────────────────────────────────
            line = json.dumps(data, default=str) + "\n"
            with LOG_FILE.open("a", encoding="utf-8") as fh:
                fh.write(line)

            _entry_count += 1

        except Exception as exc:
            print(f"[log_engine] write error: {exc}")
            return  # don't attempt push if write itself failed

    # ── Decide whether to push (outside the write lock) ────────────────────
    elapsed = time.monotonic() - _last_push_ts
    should_push = (_entry_count >= PUSH_EVERY_N) or (elapsed >= PUSH_EVERY_S)

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

        # git add logs/
        rc, out = await _run("git", "add", "logs/")
        if rc != 0:
            print(f"[log_engine] git add failed ({rc}): {out}")
            return

        # git commit — may return 1 if nothing to commit (not an error)
        rc, out = await _run(
            "git", "commit", "-m", commit_msg, "--allow-empty"
        )
        if rc != 0:
            print(f"[log_engine] git commit failed ({rc}): {out}")
            return

        # git push
        rc, out = await _run("git", "push", "origin", "HEAD")
        if rc != 0:
            print(f"[log_engine] git push failed ({rc}): {out}")
            return

        # ── Success ─────────────────────────────────────────────────────────
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
        line = json.dumps(data, default=str) + "\n"
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:
        print(f"[log_engine] sync_append error: {exc}")
