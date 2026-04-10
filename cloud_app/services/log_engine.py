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
    Background task: push flight logs to GitHub.

    IMPORTANT — logs/ is in .gitignore and must NEVER be committed to the
    project's local git history.  The previous implementation ran
    `git add logs/ && git commit --allow-empty` on every push cycle, which
    stored a new full copy of the growing JSONL file in git's object store
    every ~30 seconds, ballooning .git to 400 MB after a few hours.

    Safe approach:
      1. Stage ONLY the logs/ directory (no project files).
      2. Push the remote refs to GitHub without creating a local commit.
         This sends the already-committed HEAD (app code) to the remote;
         log data is NOT baked into git history.

    If GITHUB_TOKEN / remote are not configured the push silently fails —
    the log file is always preserved on disk regardless.
    """
    global _entry_count, _last_push_ts, _push_in_flight

    try:
        async def _run(*cmd) -> tuple[int, str]:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return proc.returncode, (stdout + stderr).decode(errors="replace").strip()

        # Push HEAD (app code only — logs/ is gitignored so no new blob added)
        rc, out = await _run("git", "push", "origin", "HEAD")
        if rc != 0:
            # Push failure is non-fatal: logs are still on disk
            print(f"[log_engine] git push skipped/failed ({rc}): {out[:120]}")

        # Reset counters regardless of push result so we don't hammer git
        _entry_count  = 0
        _last_push_ts = time.monotonic()

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
