"""Build the subprime auto credit index from on-disk ABS-EE auto-loan XMLs.

Reads every Trust / Auto Loans XML present on disk (per the listing), runs the
parser -> universe -> metrics -> index chain, writes the derived tables to CSV,
and optionally upserts them to a Postgres / Supabase project.

Usage:
    python run_index.py                          # build + write CSVs to csv/
    python run_index.py --plot                   # also save a chart to Heatmaps/
    python run_index.py --db "$DATABASE_URL"     # also upsert to Postgres
    python run_index.py --as-of 2026-06-05
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import autoLoanParser
import index as index_mod
import persist
import utility

log = logging.getLogger("absee.subprime.run")
ROOT = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a local .env into the environment (no override)."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_CACHE = "enriched_auto_loans.pkl"


def enriched_from_disk(listing: str = "Inputs/dtABS.csv", *, refresh: bool = False):
    """Parse all on-disk auto-loan XMLs into the enriched frame (cached).

    Parsing 2.6 GB of XML is slow, so the enriched result is pickled under
    ``Pickled/`` and reused on subsequent runs. Pass ``refresh=True`` (or delete
    the pickle) after downloading new filings.
    """
    cache_path = utility.PICKLED / _CACHE
    if cache_path.exists() and not refresh:
        log.info("Loading cached enriched frame from %s", cache_path)
        return utility.pickle_load([_CACHE])
    df = utility.read_listing(listing)
    raw = utility.read_ald_files(df, "Trust", "Auto Loans")
    if raw.empty:
        raise SystemExit("No auto-loan XMLs on disk. Run fetch_subprime.py first.")
    log.info("Parsed %d loan-month rows from disk", len(raw))
    enriched = autoLoanParser.append_calc_fields(autoLoanParser.clean_ald_files(raw))
    utility.pickle_save(enriched, _CACHE)
    return enriched


def build_from_disk(listing: str = "Inputs/dtABS.csv", *, refresh: bool = False):
    """Parse (or load cached) on-disk auto-loan XMLs and build the index."""
    return index_mod.build_index(enriched_from_disk(listing, refresh=refresh))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--listing", default="Inputs/dtABS.csv")
    p.add_argument("--out", default="csv", help="CSV output dir")
    p.add_argument("--as-of", default=None, help="Build date stamp (YYYY-MM-DD)")
    p.add_argument("--db", nargs="?", const="__env__", default=None,
                   help="Upsert to Postgres. Pass a DSN, or use bare --db to read "
                        "DATABASE_URL from .env / environment (keeps it off the CLI).")
    p.add_argument("--plot", action="store_true", help="Save an index chart")
    p.add_argument("--refresh", action="store_true",
                   help="Re-parse XMLs instead of using the cached enriched frame.")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")

    idx, uni, tm = build_from_disk(args.listing, refresh=args.refresh)

    print("\n=== UNIVERSE ===")
    print(uni[["wavg_fico", "original_pool", "n_loans", "qualifies", "reason"]].to_string())
    if idx.empty:
        print("\nNo qualifying trusts -> empty index.")
        return 0
    print(f"\n=== INDEX MARKS ({len(idx)} months) ===")
    cols = ["n_trusts", "delq30plus", "delq60plus", "roll_c_to_30",
            "net_loss_annl", "recovery_rate", "stress_index", "covid_flag"]
    print(idx[[c for c in cols if c in idx.columns]].round(4).to_string())

    persist.to_csv(idx, tm, uni, args.out, as_of=args.as_of)
    if args.db:
        _load_dotenv()
        dsn = os.environ.get("DATABASE_URL") if args.db == "__env__" else args.db
        if not dsn:
            raise SystemExit("--db requested but no DSN given and DATABASE_URL not set.")
        persist.to_db(idx, tm, uni, dsn=dsn, as_of=args.as_of)
        print("Upserted index_marks / trust_metrics / universe to Postgres.")
    if args.plot:
        import backtest  # noqa: PLC0415
        out = ROOT / "Heatmaps" / "subprime_index.png"
        backtest.plot_index(idx, save_to=out)
        print(f"\nSaved chart -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
