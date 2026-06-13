"""Build the subprime auto credit index from on-disk ABS-EE auto-loan XMLs.

Two build paths:

  * **incremental** (default) — process ONE deal at a time: parse its full
    monthly history, compute that deal's universe row + monthly metrics, then
    discard the loan tapes. Memory stays bounded to a single deal, and each
    deal's metrics are cached (keyed on its on-disk file set) so re-runs only
    reprocess deals whose filings changed. This is what scales to the full
    multi-year, multi-shelf history.
  * **monolithic** (``--monolithic``) — parse every tape into one frame and
    build in memory. Simple, but only viable for a few dozen files; kept for
    small runs and parity testing.

Both end at the same pooled performance + rolling-Z stress layers, write the
derived tables to CSV, and can upsert to Postgres / Supabase.

Usage:
    python run_index.py                          # incremental build -> csv/
    python run_index.py --plot                   # also save a chart
    python run_index.py --as-of 2026-06-05 --db  # upsert (reads DATABASE_URL from .env)
    python run_index.py --monolithic             # old in-memory path
    python run_index.py --refresh                # ignore caches, reprocess all
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import pickle
import sys
from pathlib import Path

import pandas as pd

import autoLoanParser
import index as index_mod
import metrics as metrics_mod
import persist
import universe as universe_mod
import utility

log = logging.getLogger("absee.subprime.run")
ROOT = Path(__file__).resolve().parent
LOAN_DIR = ROOT / "Auto Loans"

# Numeric fields coerced to float64 at read time so the per-deal frame never
# exists as a multi-GB all-object blob (the cause of the build's memory-swap
# stall on an 8 GB machine).
_const = autoLoanParser.const
INDEX_NUMERIC_COLS = (
    _const.decimalFields() + _const.integerFields() + _const.rateFields()
)
# Raw XML columns the subprime-index pipeline actually consumes (clean_ald_files
# + append_calc_fields + universe + metrics). Everything else — unused object
# string columns such as originatorName / primaryLoanServicerName / model name —
# is dropped at read time for headroom. Derived columns (monthsDelinquent,
# consumerCreditScore, beginningBalanceAtCutoffDate, …) are produced downstream
# and need not appear here. securitizationKey/shelf/reportDate are injected by
# read_ald_files after the prune, so they are intentionally absent.
INDEX_KEEP_COLS = set(
    INDEX_NUMERIC_COLS
    + _const.dateFields()
    + [
        "assetNumber",
        "securitizationKey",
        "obligorCreditScoreType",
        "obligorGeographicLocation",
        "vehicleManufacturerName",
    ]
)


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


# ---------------------------------------------------------------------------
# Incremental, per-deal build (default; scales to the full history)
# ---------------------------------------------------------------------------

def _enrich(raw: pd.DataFrame) -> pd.DataFrame:
    return autoLoanParser.append_calc_fields(autoLoanParser.clean_ald_files(raw))


def _deal_metrics(deal_rows: pd.DataFrame, *, refresh: bool):
    """Universe row + (exit-filtered) monthly metrics for ONE deal, cached.

    The cache key includes the deal's on-disk filename set, so downloading a new
    month invalidates only that deal. Returns ``(universe_row_df, tm_deal_df)``;
    ``tm_deal_df`` is None when the deal doesn't qualify (or has no on-disk data).
    """
    secname = deal_rows["secname"].iloc[0]
    on_disk = sorted(fn for fn in deal_rows["filename"] if (LOAN_DIR / fn).exists())
    if not on_disk:
        return None, None

    key = hashlib.md5(("|".join([secname, *on_disk])).encode()).hexdigest()[:12]
    cache = utility.PICKLED / f"deal_{key}.pkl"
    if cache.exists() and not refresh:
        with open(cache, "rb") as fh:
            return pickle.load(fh)

    raw = utility.read_ald_files(
        deal_rows, "Trust", "Auto Loans",
        keep_cols=INDEX_KEEP_COLS, numeric_cols=INDEX_NUMERIC_COLS,
    )
    if raw.empty:
        return None, None
    enriched = _enrich(raw)
    uni_row = universe_mod.build_universe(enriched)
    # apply_universe drops non-qualifying trusts AND post-exit months — reuse it
    # so the incremental path matches the monolithic build exactly.
    filtered = universe_mod.apply_universe(enriched, uni_row)
    tm_deal = metrics_mod.trust_month_metrics(filtered) if not filtered.empty else None

    utility.PICKLED.mkdir(parents=True, exist_ok=True)
    with open(cache, "wb") as fh:
        pickle.dump((uni_row, tm_deal), fh, protocol=pickle.HIGHEST_PROTOCOL)
    return uni_row, tm_deal


def build_incremental(listing: str = "Inputs/dtABS.csv", *, refresh: bool = False):
    """Per-deal incremental build -> (index_df, universe_df, trust_metrics_df)."""
    df = utility.read_listing(listing)
    sub = df[(df["entitytype"] == "Trust") & (df["assetclass"] == "Auto Loans")]
    deals = sorted(sub["secname"].dropna().unique())

    uni_rows, tm_parts = [], []
    n_done = 0
    for secname in deals:
        uni_row, tm_deal = _deal_metrics(sub[sub["secname"] == secname], refresh=refresh)
        if uni_row is None:
            continue
        uni_rows.append(uni_row)
        if tm_deal is not None and not tm_deal.empty:
            tm_parts.append(tm_deal)
        n_done += 1
        log.info("Deal %d/%d processed: %s", n_done, len(deals), secname)

    if not uni_rows:
        raise SystemExit("No on-disk auto-loan deals found. Run fetch_subprime.py first.")
    uni = pd.concat(uni_rows).sort_values("shelf")
    if not tm_parts:
        return pd.DataFrame(), uni, pd.DataFrame()
    tm = pd.concat(tm_parts).sort_index()
    pooled = index_mod.pool_metrics(tm)
    idx = index_mod.build_stress_index(pooled)
    return idx, uni, tm


# ---------------------------------------------------------------------------
# Monolithic build (small runs / parity testing)
# ---------------------------------------------------------------------------

_CACHE = "enriched_auto_loans.pkl"


def build_monolithic(listing: str = "Inputs/dtABS.csv", *, refresh: bool = False):
    """Parse every on-disk tape into one frame and build in memory (cached)."""
    cache_path = utility.PICKLED / _CACHE
    if cache_path.exists() and not refresh:
        log.info("Loading cached enriched frame from %s", cache_path)
        enriched = utility.pickle_load([_CACHE])
    else:
        df = utility.read_listing(listing)
        raw = utility.read_ald_files(df, "Trust", "Auto Loans")
        if raw.empty:
            raise SystemExit("No auto-loan XMLs on disk. Run fetch_subprime.py first.")
        log.info("Parsed %d loan-month rows from disk", len(raw))
        enriched = _enrich(raw)
        utility.pickle_save(enriched, _CACHE)
    return index_mod.build_index(enriched)


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
    p.add_argument("--monolithic", action="store_true",
                   help="Use the in-memory build instead of the per-deal incremental one.")
    p.add_argument("--refresh", action="store_true",
                   help="Ignore caches and reprocess all deals / re-parse XMLs.")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")

    builder = build_monolithic if args.monolithic else build_incremental
    idx, uni, tm = builder(args.listing, refresh=args.refresh)

    print("\n=== UNIVERSE ===")
    print(uni[["wavg_fico", "original_pool", "n_loans", "qualifies", "reason"]].to_string())
    if idx.empty:
        print("\nNo qualifying trusts -> empty index.")
        return 0
    print(f"\n=== INDEX MARKS ({len(idx)} months) ===")
    cols = ["n_trusts", "delq30plus", "delq60plus", "roll_c_to_30",
            "net_loss_annl", "recovery_rate", "stress_index", "stress_index_pca", "covid_flag"]
    shown = idx[[c for c in cols if c in idx.columns]].round(4)
    print((shown.iloc[::max(1, len(shown) // 30)] if len(shown) > 40 else shown).to_string())

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
