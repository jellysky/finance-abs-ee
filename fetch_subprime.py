"""Downloader for subprime auto-loan ABS-EE XMLs (scoped or full panel).

Two modes against an existing listing (``Inputs/dtABS.csv``, populated by
``utility.py search`` / ``history``):

  * **scoped** (default) — recent filings for a couple of shelves; small and
    fast (the original validation pull).
  * **panel** (``--panel``) — select a representative set of subprime deals
    spanning vintages and download their *full* monthly history. This feeds the
    multi-year index (stress layer needs >=24 months). Picks ~``per_shelf``
    deals per shelf, chained across vintage buckets for continuous calendar
    coverage. Carvana is restricted to the subprime N-series (its P-series is
    prime).

Usage:
    python fetch_subprime.py                          # scoped: Santander+Exeter, recent
    python fetch_subprime.py --panel --dry-run        # show the full panel selection
    python fetch_subprime.py --panel                  # download the full panel
    python fetch_subprime.py --panel --per-shelf 4
"""
from __future__ import annotations

import argparse
import logging
import re
import sys

import pandas as pd

import utility

log = logging.getLogger("absee.subprime.fetch")

DEFAULT_SHELVES = ["Santander Drive", "Exeter"]
# Full subprime panel shelves.
PANEL_SHELVES = ["Santander Drive", "Exeter", "AmeriCredit", "Bridgecrest", "Carvana"]
# Vintage buckets (deals live ~4-5yr, so these chain to cover ~2019-present).
_BUCKETS = [("early", lambda y: y is not None and y <= 2021),
            ("mid", lambda y: y is not None and 2022 <= y <= 2023),
            ("late", lambda y: y is not None and y >= 2024)]


def _vintage(secname: str) -> int | None:
    m = re.search(r"\b(20\d{2})-", secname)
    return int(m.group(1)) if m else None


def _carvana_is_subprime(secname: str) -> bool:
    """Carvana N-series = subprime; P-series = prime (exclude)."""
    return bool(re.search(r"20\d{2}-N\d", secname))


def _trust_loans(listing: pd.DataFrame) -> pd.DataFrame:
    return listing[(listing["entitytype"] == "Trust")
                   & (listing["assetclass"] == "Auto Loans")].copy()


def select_filings(listing: pd.DataFrame, shelves: list[str], since: str | None) -> pd.DataFrame:
    """Scoped mode: Trust/Auto-Loan rows matching shelves + optional date floor."""
    base = _trust_loans(listing)
    mask = base["secname"].str.contains("|".join(shelves), case=False, regex=True, na=False)
    if since:
        report = pd.to_datetime(base["reportdate"].astype(str), format="%Y%m%d", errors="coerce")
        mask &= report >= pd.Timestamp(since)
    return base.loc[mask]


def select_panel(listing: pd.DataFrame, shelves: list[str], per_shelf: int = 3) -> tuple[pd.DataFrame, list[str]]:
    """Pick a representative subprime panel: ~per_shelf deals/shelf across vintages.

    For each shelf, partition its deals into vintage buckets (early/mid/late) and
    pick the deal with the most filings in each bucket (most filings = longest,
    most complete history). Returns (all filings for the picked deals, deal list).
    """
    base = _trust_loans(listing)
    picked: list[str] = []
    for shelf in shelves:
        deals = base[base["secname"].str.contains(shelf, case=False, regex=False, na=False)]
        if shelf == "Carvana":
            deals = deals[deals["secname"].map(_carvana_is_subprime)]
        if deals.empty:
            log.warning("No deals for shelf %s", shelf)
            continue
        counts = deals.groupby("secname")["url"].nunique()  # filings per deal
        info = pd.DataFrame({"n": counts})
        info["vintage"] = [_vintage(s) for s in info.index]
        for _bname, pred in _BUCKETS:
            bucket = info[info["vintage"].map(pred)].sort_values("n", ascending=False)
            taken = 0
            for secname in bucket.index:
                if secname in picked:
                    continue
                picked.append(secname)
                taken += 1
                if taken >= max(1, per_shelf // len(_BUCKETS)) + (1 if per_shelf % len(_BUCKETS) else 0):
                    break
    sel = base[base["secname"].isin(picked)]
    return sel, picked


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--listing", default="Inputs/dtABS.csv")
    p.add_argument("--panel", action="store_true", help="Full-history panel mode.")
    p.add_argument("--shelves", nargs="+", default=None,
                   help="Shelf fragments (default: scoped=Santander+Exeter, panel=all 5).")
    p.add_argument("--per-shelf", type=int, default=3, help="Deals per shelf in panel mode.")
    p.add_argument("--since", default="2025-06-01",
                   help="Scoped mode date floor (YYYY-MM-DD); '' for all. Ignored in panel mode.")
    p.add_argument("--dry-run", action="store_true", help="List selection without downloading.")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")

    listing = utility.read_listing(args.listing)

    if args.panel:
        shelves = args.shelves or PANEL_SHELVES
        selected, deals = select_panel(listing, shelves, args.per_shelf)
        log.info("Panel: %d deals, %d filings (full history each):", len(deals), len(selected))
        for d in sorted(deals):
            n = selected[selected["secname"] == d]["url"].nunique()
            log.info("  %-55s %3d filings", d, n)
    else:
        shelves = args.shelves or DEFAULT_SHELVES
        selected = select_filings(listing, shelves, args.since or None)
        log.info("Scoped: %d filing(s) across %s", len(selected), shelves)

    if selected.empty:
        log.warning("Nothing matched. Has the listing been populated (search/history)?")
        return 1

    est_gb = selected["url"].nunique() * 0.19  # ~190 MB/file
    log.info("Unique files: %d  (~%.0f GB at ~190 MB/file)", selected["url"].nunique(), est_gb)
    if args.dry_run:
        return 0

    utility.download_filings(selected, entity_types=["Trust"], asset_classes=["Auto Loans"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
