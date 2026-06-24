"""Push the latest Serention Net-Loss Index (NLI) to the on-chain net-loss oracle (Sepolia),
which the NetLossFuture strip reads as its mark. Reads web/data/nli.json (built by
web/build_nli.py). Idempotent: skips if unchanged.

    python push_nli.py            # push if changed
    python push_nli.py --dry-run
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
ORACLE = "0xb1405f63aadf7d87d81dd6f18590bd7fd7d6e542"  # net-loss rate oracle (Sepolia)


def _env() -> dict:
    out: dict[str, str] = {}
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"').strip("'")
    return out


def latest_nli() -> float:
    d = json.loads((ROOT / "web" / "data" / "nli.json").read_text())
    return float(d["series"][-1]["nli"])  # %/yr


def onchain(rpc: str) -> int:
    out = subprocess.run(
        [CAST, "call", ORACLE, "latestRoundData()(uint80,int256,uint256,uint256,uint80)", "--rpc-url", rpc],
        capture_output=True, text=True, check=True).stdout.strip().splitlines()
    return int(out[1].split()[0])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    env = _env()
    rpc, key = env["SEPOLIA_RPC_URL"], env["PRIVATE_KEY"]

    nli = latest_nli()
    answer = int(round(nli * 1e8))
    current = onchain(rpc)
    print(f"latest NLI = {nli:.3f}%/yr -> answer {answer}; on-chain {current} ({current/1e8:.3f})")
    if answer == current:
        print("unchanged — nothing to push.")
        return 0
    if args.dry_run:
        print(f"[dry-run] would push setAnswer({answer})")
        return 0
    r = subprocess.run([CAST, "send", ORACLE, "setAnswer(int256)", str(answer),
                        "--rpc-url", rpc, "--private-key", key], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr); raise SystemExit("cast send failed")
    for line in r.stdout.splitlines():
        if "transactionHash" in line:
            print(line.strip())
    print(f"pushed NLI {nli:.3f}%/yr.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
