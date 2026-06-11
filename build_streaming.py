"""Disk-safe streaming build of the subprime auto index.

Motivated by the 2026-06-11 disk-full incident: the raw ABS-EE XMLs are ~100 GB
if all deals sit on disk at once. This build never holds more than ONE deal:

  for each panel deal not already cached:
      download its monthly XMLs  ->  compute (universe_row, trust_month_metrics)
      cache the per-deal result  ->  DELETE that deal's XMLs before the next deal

Deals already processed (their ``deal_*.pkl`` caches, which hold
``(uni_row, tm_deal)``) are loaded directly and never re-downloaded. So a re-run
only fetches genuinely new deals, one at a time, peak disk ~= one deal (~7 GB).

Then it pools all per-deal metrics, builds the stress index, writes csv/ and
(optionally) upserts to Postgres — identical downstream math to run_index.py.

Usage:
    python build_streaming.py --db --plot --as-of 2026-06-11
    python build_streaming.py --dry-run        # show what would download
"""
from __future__ import annotations

import argparse
import logging
import pickle
import shutil
import sys
from pathlib import Path

import pandas as pd

import fetch_subprime
import index as index_mod
import persist
import run_index
import utility

log = logging.getLogger("absee.subprime.stream")
ROOT = run_index.ROOT
LOAN_DIR = run_index.LOAN_DIR


def _disk_free_gb() -> float:
    return shutil.disk_usage(ROOT).free / 1e9


def load_cached_deals() -> dict[str, tuple]:
    """secname -> (uni_row, tm_deal) from deal_*.pkl, keeping the longest history."""
    out: dict[str, tuple] = {}
    for p in sorted(utility.PICKLED.glob("deal_*.pkl")):
        try:
            with open(p, "rb") as fh:
                uni_row, tm_deal = pickle.load(fh)
        except Exception as e:  # noqa: BLE001
            log.warning("Skipping unreadable cache %s: %s", p.name, e)
            continue
        if uni_row is None or uni_row.empty:
            continue
        secname = uni_row.index[0]
        nmonths = 0 if tm_deal is None else len(tm_deal)
        if secname not in out or nmonths > out[secname][2]:
            out[secname] = (uni_row, tm_deal, nmonths)
    return {k: (v[0], v[1]) for k, v in out.items()}


def stream_deal(deal_rows: pd.DataFrame, secname: str) -> tuple:
    """Download one deal, build its metrics, then delete its XMLs. (uni_row, tm_deal)."""
    log.info("Downloading deal: %s (disk free %.0f GB)", secname, _disk_free_gb())
    utility.download_filings(deal_rows, ["Trust"], ["Auto Loans"])
    try:
        uni_row, tm_deal = run_index._deal_metrics(deal_rows, refresh=False)
    finally:
        removed = 0
        for fn in deal_rows["filename"].dropna().unique():
            f = LOAN_DIR / fn
            if f.exists():
                f.unlink()
                removed += 1
        log.info("Deleted %d XMLs for %s (disk free now %.0f GB)",
                 removed, secname, _disk_free_gb())
    return uni_row, tm_deal


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--listing", default="Inputs/dtABS.csv")
    p.add_argument("--per-shelf", type=int, default=6)
    p.add_argument("--out", default="csv")
    p.add_argument("--as-of", default="2026-06-11")
    p.add_argument("--db", action="store_true", help="Upsert to DATABASE_URL from .env")
    p.add_argument("--plot", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")

    listing = utility.read_listing(args.listing)
    sub = listing[(listing["entitytype"] == "Trust") & (listing["assetclass"] == "Auto Loans")]

    _, panel = fetch_subprime.select_panel(listing, fetch_subprime.PANEL_SHELVES, args.per_shelf)
    cached = load_cached_deals()
    target = sorted(set(panel) | set(cached))
    to_stream = [d for d in target if d not in cached]

    log.info("Target constituents: %d (%d cached, %d to download)",
             len(target), len(target) - len(to_stream), len(to_stream))
    for d in to_stream:
        log.info("  will download: %s (%d filings)", d, sub[sub["secname"] == d]["url"].nunique())
    if args.dry_run:
        return 0

    uni_rows, tm_parts = [], []
    for secname in target:
        if secname in cached:
            uni_row, tm_deal = cached[secname]
            log.info("Reusing cache: %s", secname)
        else:
            uni_row, tm_deal = stream_deal(sub[sub["secname"] == secname], secname)
        if uni_row is None or uni_row.empty:
            continue
        uni_rows.append(uni_row)
        if tm_deal is not None and not tm_deal.empty:
            tm_parts.append(tm_deal)

    uni = pd.concat(uni_rows).sort_values("shelf")
    tm = pd.concat(tm_parts).sort_index()
    pooled = index_mod.pool_metrics(tm)
    idx = index_mod.build_stress_index(pooled)

    print(f"\n=== INDEX BUILT: {len(idx)} months, {int(uni['qualifies'].sum())} qualifying trusts ===")
    print(uni[["shelf", "wavg_fico", "first_month", "last_month", "qualifies"]].to_string())

    persist.to_csv(idx, tm, uni, args.out, as_of=args.as_of)
    if args.db:
        run_index._load_dotenv()
        import os
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise SystemExit("--db requested but DATABASE_URL not set in .env")
        persist.to_db(idx, tm, uni, dsn=dsn, as_of=args.as_of)
        print("Upserted index_marks / trust_metrics / universe to Postgres.")
    if args.plot:
        import backtest
        out = ROOT / "Heatmaps" / "subprime_index.png"
        backtest.plot_index(idx, save_to=out)
        print(f"Saved chart -> {out}")
    print(f"\nFinal disk free: {_disk_free_gb():.0f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
