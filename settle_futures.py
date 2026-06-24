"""Settle any Net-Loss Future series whose settlement (print) date has passed, to the realized
NLI for its reference month (from abs.nli_monthly). Idempotent. Reads strip.json.

    python settle_futures.py            # settle due contracts
    python settle_futures.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent
CAST = str(Path.home() / ".foundry" / "bin" / "cast")


def _load_env(p: Path) -> dict:
    out: dict[str, str] = {}
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); out[k] = v.strip().strip('"').strip("'")
    return out


ENV = {**_load_env(ROOT / ".env"), **_load_env(ROOT / "contracts" / ".env")}


def realized_nli(ref_month: str) -> float | None:
    with psycopg.connect(ENV["DATABASE_URL"]) as c, c.cursor() as cur:
        cur.execute("select nli_pct from abs.nli_monthly where report_month = %s", (ref_month,))
        r = cur.fetchone()
    return float(r[0]) if r and r[0] is not None else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rpc, key = ENV["SEPOLIA_RPC_URL"], ENV["PRIVATE_KEY"]
    strip = json.loads((ROOT / "strip.json").read_text())
    now = int(time.time())

    for s in strip:
        addr, label, ref = s["address"], s["label"], s["refMonth"]
        settled = subprocess.run([CAST, "call", addr, "settled()(bool)", "--rpc-url", rpc],
                                 capture_output=True, text=True, check=True).stdout.strip() == "true"
        if settled:
            print(f"{label}: already settled"); continue
        if now < s["settleTs"]:
            due = time.strftime("%Y-%m-%d", time.gmtime(s["settleTs"]))
            print(f"{label}: not due (settles {due})"); continue
        nli = realized_nli(ref)
        if nli is None:
            print(f"{label}: DUE but {ref[:7]} not in abs.nli_monthly yet — load new filings, rebuild NLI, rerun")
            continue
        answer = int(round(nli * 1e8))
        if args.dry_run:
            print(f"{label}: [dry-run] would settle to {nli:.3f}%/yr (answer {answer})"); continue
        r = subprocess.run([CAST, "send", addr, "settle(int256)", str(answer),
                            "--rpc-url", rpc, "--private-key", key], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"{label}: settle FAILED: {r.stderr.strip()[:120]}", file=sys.stderr); continue
        print(f"{label}: SETTLED to {nli:.3f}%/yr")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
