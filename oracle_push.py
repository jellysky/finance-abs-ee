"""Off-chain pusher: publish the latest Serention CASH NET YIELD to the on-chain
AggregatorV3 oracle (SerentionIndexOracle) on Sepolia. MarginedIndex reads this feed,
so the on-chain derivative tracks the net-yield index (the chosen tradeable series).

    answer = round(net_yield_pct * 1e8)   (oracle decimals = 8; net yield is %/yr, e.g. 9.54)

Reads the latest cash net yield from web/data/netyield.json (built by build_netyield.py
from the persisted abs.loan_month_agg marks). Idempotent: skips the send if unchanged.
Replaces the sunset Chainlink Functions feed; the AggregatorV3 interface is unchanged so
a decentralized feed (CRE on mainnet) can swap in later.

    python oracle_push.py            # compute, compare, push if changed
    python oracle_push.py --dry-run  # compute + compare only, no transaction
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / "contracts" / ".env"
CAST = str(Path.home() / ".foundry" / "bin" / "cast")
ORACLE = "0x99e3Eee494164F28781cDF8612bce410CaBA0826"  # SerentionIndexOracle (Sepolia)


def _env() -> dict:
    out: dict[str, str] = {}
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"').strip("'")
    return out


def latest_net_yield() -> float:
    d = json.loads((ROOT / "web" / "data" / "netyield.json").read_text())
    return float(d["series"][-1]["net_yield"])  # cash basis, %/yr


def to_answer(net_yield_pct: float) -> int:
    return int(round(max(net_yield_pct, 0.01) * 1e8))


def onchain_answer(rpc: str) -> int:
    out = subprocess.run(
        [CAST, "call", ORACLE, "latestRoundData()(uint80,int256,uint256,uint256,uint80)",
         "--rpc-url", rpc],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    return int(out[1].split()[0])


def main() -> int:
    ap = argparse.ArgumentParser(description="Push latest cash net yield to the oracle.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    env = _env()
    rpc, key = env["SEPOLIA_RPC_URL"], env["PRIVATE_KEY"]

    ny = latest_net_yield()
    answer = to_answer(ny)
    current = onchain_answer(rpc)
    print(f"latest cash net yield = {ny:.3f}%/yr  ->  answer {answer}")
    print(f"on-chain answer = {current}  (level {current / 1e8:.3f})")

    if answer == current:
        print("unchanged — nothing to push.")
        return 0
    if args.dry_run:
        print(f"[dry-run] would push setAnswer({answer})")
        return 0

    r = subprocess.run(
        [CAST, "send", ORACLE, "setAnswer(int256)", str(answer),
         "--rpc-url", rpc, "--private-key", key],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise SystemExit("cast send failed")
    for line in r.stdout.splitlines():
        if "transactionHash" in line:
            print(line.strip())
    print(f"pushed setAnswer({answer}) — net yield {ny:.3f}%/yr.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
