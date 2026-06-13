"""Off-chain pusher: publish the latest Serention stress index to the on-chain
AggregatorV3 oracle (SerentionIndexOracle) on Sepolia.

Replaces the sunset Chainlink Functions feed (testnet Functions was decommissioned
2026-06-02). The oracle keeps the standard Chainlink AggregatorV3 interface, so a
decentralized feed (e.g. Chainlink CRE on mainnet) can swap in later with no change
to MarginedIndex.

Convention (matches the contract seed and the DON JS we wrote):
    level  = 100 + 25 * stress_index    (z-score average; floored > 0)
    answer = round(level * 1e8)          (oracle decimals = 8)

Reads the latest stress from csv/index_marks.csv (the built index). Idempotent:
skips the send if the on-chain answer already matches. Run on the index cadence
(e.g. monthly cron, or after each `run_index.py` rebuild).

    python oracle_push.py            # compute, compare, push if changed
    python oracle_push.py --dry-run  # compute + compare only, no transaction
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
ENV = ROOT / "contracts" / ".env"
CAST = str(Path.home() / ".foundry" / "bin" / "cast")

ORACLE = "0x99e3Eee494164F28781cDF8612bce410CaBA0826"  # SerentionIndexOracle (Sepolia)
BASE, SCALE = 100.0, 25.0  # affine rebasing: level = BASE + SCALE * stress


def _env() -> dict:
    out: dict[str, str] = {}
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"').strip("'")
    return out


def latest_stress() -> float:
    s = pd.read_csv(ROOT / "csv" / "index_marks.csv")["stress_index"].dropna()
    if s.empty:
        raise SystemExit("no stress_index in csv/index_marks.csv")
    return float(s.iloc[-1])


def to_answer(stress: float) -> int:
    level = BASE + SCALE * stress
    return int(round(max(level, 0.01) * 1e8))


def onchain_answer(rpc: str) -> int:
    out = subprocess.run(
        [CAST, "call", ORACLE, "latestRoundData()(uint80,int256,uint256,uint256,uint80)",
         "--rpc-url", rpc],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    return int(out[1].split()[0])  # 2nd return value is the int256 answer


def main() -> int:
    ap = argparse.ArgumentParser(description="Push latest stress index to the oracle.")
    ap.add_argument("--dry-run", action="store_true", help="compute + compare only")
    args = ap.parse_args()

    env = _env()
    rpc, key = env["SEPOLIA_RPC_URL"], env["PRIVATE_KEY"]

    stress = latest_stress()
    answer = to_answer(stress)
    current = onchain_answer(rpc)
    print(f"latest stress = {stress:+.3f}  ->  level {answer / 1e8:.2f}  ->  answer {answer}")
    print(f"on-chain answer = {current}  (level {current / 1e8:.2f})")

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
    print(f"pushed setAnswer({answer}) (level {answer / 1e8:.2f}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
