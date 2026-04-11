#!/usr/bin/env bash
# Post-merge setup script — runs automatically after a task agent merge.
# Installs/updates Python dependencies so the app is always ready.
set -e

echo "[post-merge] Installing Python dependencies..."
pip install -q -r requirements.txt

echo "[post-merge] Done."
