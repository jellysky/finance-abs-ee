#!/usr/bin/env bash
# Serention monthly job: rebuild the NLI from loan_months, push it to the on-chain
# net-loss oracle, and settle any Net-Loss Future whose print date has passed.
# Scheduled via ~/Library/LaunchAgents/fi.serention.netloss.plist (24th, 07:00).
# NOTE: this recomputes/pushes from CURRENT data. Ingesting NEW ABS-EE filings
# (load_loans.py for the new month) is a separate, heavier prerequisite — run it
# first when new tapes post, so build_nli has fresh months to settle against.
set -uo pipefail
cd "/Users/koushik/Programming/Pycharm Projects/Finance/ABS-EE" || exit 1
export PATH="$HOME/.foundry/bin:$PATH"
PY=./.venv/bin/python

echo "===== $(date) · Serention monthly run ====="
$PY web/build_nli.py      && \
$PY push_nli.py           && \
$PY settle_futures.py
echo "===== $(date) · done ====="
