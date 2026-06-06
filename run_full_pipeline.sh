#!/bin/bash
# Full subprime-index pipeline: download the panel, build incrementally, load to
# Postgres, save chart. Designed to run detached under `caffeinate` so it
# survives sleep/session-end. Fully resumable: downloads skip on-disk files and
# the build skips cached deals, so re-launching continues where it left off.
cd "/Users/koushik/Programming/Pycharm Projects/Finance/ABS-EE" || exit 1
source .venv/bin/activate 2>/dev/null

echo "[$(date '+%F %T')] ===== STAGE 1: download panel ====="
python fetch_subprime.py --panel
echo "[$(date '+%F %T')] download stage exit=$?"

echo "[$(date '+%F %T')] ===== STAGE 2: incremental build + load + chart ====="
python run_index.py --as-of 2026-06-05 --db --plot
echo "[$(date '+%F %T')] build stage exit=$?"

echo "[$(date '+%F %T')] ===== PIPELINE COMPLETE ====="
