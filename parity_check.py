"""Parity + memory check for the read_ald_files optimization.

Proves the per-file numeric coercion + column pruning added to
``utility.read_ald_files`` produce a byte-identical enriched frame (on the
columns the index keeps) and identical universe / trust-metric outputs versus
the original all-object read — then reports the peak-memory and wall-time
difference on a real on-disk deal.

Run: ``python parity_check.py`` (optionally ``python parity_check.py <secname>``).
"""
from __future__ import annotations

import sys
import time
import tracemalloc

import numpy as np
import pandas as pd

import autoLoanParser
import metrics as metrics_mod
import run_index
import universe as universe_mod
import utility

SORT_KEYS = ["securitizationKey", "assetNumber", "reportingPeriodBeginningDate"]


def _enrich(raw: pd.DataFrame) -> pd.DataFrame:
    return autoLoanParser.append_calc_fields(autoLoanParser.clean_ald_files(raw))


def _canon(df: pd.DataFrame) -> pd.DataFrame:
    keys = [c for c in SORT_KEYS if c in df.columns]
    return df.sort_values(keys).reset_index(drop=True) if keys else df.reset_index(drop=True)


def _pick_smallest_deal(sub: pd.DataFrame) -> str:
    """Secname of the on-disk trust with the fewest files (fast to parity-check)."""
    counts = {}
    for secname, rows in sub.groupby("secname"):
        n = sum(1 for fn in rows["filename"] if (run_index.LOAN_DIR / fn).exists())
        if n:
            counts[secname] = n
    if not counts:
        raise SystemExit("No on-disk auto-loan deals found.")
    return min(counts, key=counts.get)


def _compare_series(a: pd.Series, b: pd.Series, col: str) -> str | None:
    """Return an error string if a != b (NaN-aware, exact for floats), else None."""
    if len(a) != len(b):
        return f"{col}: length {len(a)} != {len(b)}"
    if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
        av, bv = a.to_numpy(dtype="float64"), b.to_numpy(dtype="float64")
        both_nan = np.isnan(av) & np.isnan(bv)
        if not np.array_equal(np.where(both_nan, 0.0, av), np.where(both_nan, 0.0, bv)):
            n = int((~(both_nan | (av == bv))).sum())
            return f"{col}: {n} numeric value(s) differ (max abs {np.nanmax(np.abs(av - bv)):.3g})"
        return None
    # object / datetime: align NaN/NaT then compare element-wise
    if not a.reset_index(drop=True).equals(b.reset_index(drop=True)):
        n = int((a.to_numpy() != b.to_numpy()).sum())
        return f"{col}: {n} non-numeric value(s) differ"
    return None


def main() -> int:
    df = utility.read_listing("Inputs/dtABS.csv")
    sub = df[(df["entitytype"] == "Trust") & (df["assetclass"] == "Auto Loans")]
    secname = sys.argv[1] if len(sys.argv) > 1 else _pick_smallest_deal(sub)
    rows = sub[sub["secname"] == secname]
    print(f"Parity deal: {secname}  ({len(rows)} filings listed)\n")

    # --- OLD path: full all-object read -------------------------------------
    tracemalloc.start()
    t0 = time.time()
    raw_old = utility.read_ald_files(rows, "Trust", "Auto Loans")
    enr_old = _enrich(raw_old)
    old_t = time.time() - t0
    old_peak = tracemalloc.get_traced_memory()[1] / 1e6
    tracemalloc.stop()
    print(f"OLD  read+enrich: {old_t:6.1f}s  peak {old_peak:7.0f} MB  "
          f"raw cols={raw_old.shape[1]} rows={len(raw_old)}")

    # --- NEW path: pruned + per-file coercion -------------------------------
    tracemalloc.start()
    t0 = time.time()
    raw_new = utility.read_ald_files(
        rows, "Trust", "Auto Loans",
        keep_cols=run_index.INDEX_KEEP_COLS, numeric_cols=run_index.INDEX_NUMERIC_COLS,
    )
    enr_new = _enrich(raw_new)
    new_t = time.time() - t0
    new_peak = tracemalloc.get_traced_memory()[1] / 1e6
    tracemalloc.stop()
    print(f"NEW  read+enrich: {new_t:6.1f}s  peak {new_peak:7.0f} MB  "
          f"raw cols={raw_new.shape[1]} rows={len(raw_new)}")
    print(f"\n--> peak memory {old_peak / max(new_peak, 1):.1f}x lower, "
          f"{old_t / max(new_t, 1e-9):.1f}x faster on this deal\n")

    # --- Enriched-frame parity on every column the NEW path keeps -----------
    a, b = _canon(enr_old), _canon(enr_new)
    errors = []
    missing = [c for c in b.columns if c not in a.columns]
    if missing:
        errors.append(f"columns present in NEW but absent in OLD: {missing}")
    for col in b.columns:
        if col in a.columns:
            err = _compare_series(a[col], b[col], col)
            if err:
                errors.append(err)
    dropped = [c for c in a.columns if c not in b.columns]
    print(f"Enriched parity: NEW keeps {len(b.columns)} cols, "
          f"drops {len(dropped)} unused ({', '.join(sorted(dropped)[:8])}"
          f"{'…' if len(dropped) > 8 else ''})")

    # --- Universe + metrics parity ------------------------------------------
    uni_old = universe_mod.build_universe(enr_old)
    uni_new = universe_mod.build_universe(enr_new)
    if not uni_old.round(6).equals(uni_new.round(6)):
        errors.append("build_universe output differs")
    f_old = universe_mod.apply_universe(enr_old, uni_old)
    f_new = universe_mod.apply_universe(enr_new, uni_new)
    if not f_old.empty:
        tm_old = metrics_mod.trust_month_metrics(f_old)
        tm_new = metrics_mod.trust_month_metrics(f_new)
        diff = (tm_old.fillna(0) - tm_new.fillna(0)).abs().to_numpy().max()
        print(f"trust_month_metrics max abs diff: {diff:.3g}")
        if diff > 1e-9:
            errors.append(f"trust_month_metrics differ (max {diff:.3g})")
    else:
        print("(deal does not qualify — universe/metrics parity is the empty-frame case)")

    print()
    if errors:
        print("PARITY FAILED:")
        for e in errors:
            print("  -", e)
        return 1
    print("PARITY OK — optimized read is identical on every kept column, "
          "universe, and trust-month metric.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
