"""Scoped downloader for subprime auto-loan ABS-EE XMLs.

Filters an existing listing (``Inputs/dtABS.csv``, populated by
``utility.py search``) down to selected subprime shelves and a recent date
window, then downloads just those trust filings. Keeps the first pull small
and validatable rather than fetching the full 2016+ history of every shelf.

Usage:
    python fetch_subprime.py                      # Santander Drive + Exeter, last ~12 months
    python fetch_subprime.py --shelves Santander Exeter AmeriCredit
    python fetch_subprime.py --since 2024-06-01 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

import utility

log = logging.getLogger("absee.subprime.fetch")

# Default shelves for the first scoped pull (regex, case-insensitive).
DEFAULT_SHELVES = ["Santander Drive", "Exeter"]


def select_filings(
    listing: pd.DataFrame,
    shelves: list[str],
    since: str | None,
) -> pd.DataFrame:
    """Filter a listing to Trust / Auto Loans rows matching shelves + date."""
    mask = (listing["entitytype"] == "Trust") & (listing["assetclass"] == "Auto Loans")
    shelf_re = "|".join(shelves)
    mask &= listing["secname"].str.contains(shelf_re, case=False, regex=True, na=False)
    if since:
        report = pd.to_datetime(listing["reportdate"].astype(str), format="%Y%m%d",
                                errors="coerce")
        mask &= report >= pd.Timestamp(since)
    return listing.loc[mask].copy()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--listing", default="Inputs/dtABS.csv")
    p.add_argument("--shelves", nargs="+", default=DEFAULT_SHELVES,
                   help="Shelf name fragments (regex, case-insensitive).")
    p.add_argument("--since", default="2025-06-01",
                   help="Only filings on/after this date (YYYY-MM-DD); '' for all.")
    p.add_argument("--dry-run", action="store_true",
                   help="List what would be downloaded without fetching.")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")

    listing = utility.read_listing(args.listing)
    selected = select_filings(listing, args.shelves, args.since or None)

    by_shelf = selected.groupby(selected["secname"].str.extract(
        r"^(.*?)(?:\s+\d{4})", expand=False).fillna(selected["secname"])
    ).size()
    log.info("Selected %d filing(s) across shelves:\n%s", len(selected),
             by_shelf.to_string())

    if selected.empty:
        log.warning("Nothing matched. Has `utility.py search` populated the listing yet?")
        return 1
    if args.dry_run:
        for _, r in selected.sort_values(["secname", "reportdate"]).iterrows():
            log.info("  would fetch: %s  %s", r["reportdate"], r["secname"])
        return 0

    utility.download_filings(selected, entity_types=["Trust"], asset_classes=["Auto Loans"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
