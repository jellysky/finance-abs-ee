"""Prototype: fixed-seasoning-cohort variant of the subprime auto index (option a).

Problem it solves: the raw pooled *absolute* level tracks the average loan age
of the constituent deals (subprime losses follow a hump over seasoning), so a
calendar-time move can be aging, not credit. See Heatmaps/absolute_vs_fed.png.

Fix: only let each deal contribute in a stable loan-age window (default months
6-30 since issuance). At every calendar month the contributing deal-months then
span a consistent seasoning band, so the balance-weighted pool age stays roughly
flat and the absolute level becomes a credit signal rather than a seasoning one.

Built from the derived tables (csv/trust_metrics.csv + csv/universe.csv) and the
real pooling/stress code (index.pool_metrics / build_stress_index), so it stays
consistent with the production pipeline. Then re-validated vs the NY Fed series.

Run: ``python cohort_index.py [age_lo age_hi]``  ->  Heatmaps/cohort_index.png
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

import fed_compare as fc
import index as index_mod
from plot_index_vs_fed import _hhdc_auto

ROOT = Path(__file__).resolve().parent
COVID0, COVID1 = pd.Timestamp("2020-04-01"), pd.Timestamp("2020-12-31")
AGE_LO, AGE_HI = 6, 30  # default seasoning window (months since issuance)


def load_trust_metrics() -> tuple[pd.DataFrame, pd.Series]:
    """Return (trust_metrics MultiIndexed for pool_metrics, age-by-(trust,month))."""
    tm = pd.read_csv(ROOT / "csv" / "trust_metrics.csv", parse_dates=["month"])
    u = pd.read_csv(ROOT / "csv" / "universe.csv", parse_dates=["first_month"])
    issue = dict(zip(u["securitization_key"], u["first_month"]))
    iss = tm["securitization_key"].map(issue)
    tm["age"] = ((tm["month"].dt.year - iss.dt.year) * 12
                 + (tm["month"].dt.month - iss.dt.month))
    tm = tm.set_index(["securitization_key", "month"])
    tm.index = tm.index.set_names(["securitizationKey", "month"])
    return tm, tm["age"]


def wavg_age(tm: pd.DataFrame) -> pd.Series:
    """Balance-weighted average pool age per calendar month."""
    w = tm["pool_beg_balance"].fillna(0.0)
    aw = (tm["age"] * w).groupby(level="month").sum()
    return aw / w.groupby(level="month").sum()


def build(tm: pd.DataFrame) -> pd.DataFrame:
    """Pool -> stress index from a (possibly filtered) trust-metrics frame."""
    pooled = index_mod.pool_metrics(tm.drop(columns=["age"]))
    return index_mod.build_stress_index(pooled)


def to_quarterly(idx: pd.DataFrame) -> pd.DataFrame:
    idx = idx.reset_index().rename(columns={"index": "month"})
    idx["month"] = pd.to_datetime(idx["month"])
    idx["q"] = idx["month"].dt.to_period("Q")
    qm = fc.quarterly_metrics(idx)
    qm.index = qm.index.to_timestamp(how="end").normalize()
    return qm


def main() -> int:
    import matplotlib.pyplot as plt
    from matplotlib.dates import YearLocator, DateFormatter

    lo, hi = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (AGE_LO, AGE_HI)
    tm, age = load_trust_metrics()
    full = tm
    cohort = tm[(age >= lo) & (age <= hi)]

    idx_full = build(full)
    idx_coh = build(cohort)
    age_full = wavg_age(full)
    age_coh = wavg_age(cohort)

    # Coverage of the cohort variant.
    cov = idx_coh["n_trusts"].dropna()
    print(f"Cohort window: ages {lo}-{hi} months since issuance")
    print(f"Cohort coverage: {len(cov)} months with >=1 trust, "
          f"{int((cov >= 3).sum())} with >=3, "
          f"span {cov.index.min():%Y-%m}..{cov.index.max():%Y-%m}")
    print(f"Avg-pool-age stability (std of monthly wavg age):  "
          f"full={age_full.std():.1f}mo  cohort={age_coh.std():.1f}mo")
    print(f"Net-loss level (mean annualized %, 2022+):  "
          f"full={idx_full.loc['2022':,'net_loss_annl'].mean()*100:.1f}%  "
          f"cohort={idx_coh.loc['2022':,'net_loss_annl'].mean()*100:.1f}%")

    # --- Re-validate cohort vs Fed (correlations on overlapping quarters) ---
    fed_sub = fc.load_fed_subprime_below620(); fed_sub.index = fed_sub.index.to_timestamp(how="end").normalize()
    fed_90 = fc.load_fed_auto_90plus(); fed_90.index = fed_90.index.to_timestamp(how="end").normalize()
    qm_full, qm_coh = to_quarterly(idx_full), to_quarterly(idx_coh)
    print("\nCorrelation vs Fed (Pearson r, >=4-trust period) — full vs cohort:")
    for key, lab in [("delq30plus", "30+ DPD"), ("net_loss_annl", "net loss"),
                     ("roll_c_to_30_q", "roll C->30+")]:
        for bench, bl in [(fed_sub, "Fed<620"), (fed_90, "FedAuto90")]:
            rf = fc._corr(qm_full[qm_full["n_trusts"] >= 4][key], bench)[0]
            rc = fc._corr(qm_coh[qm_coh["n_trusts"] >= 2][key], bench)[0]
            print(f"  {lab:11s} vs {bl:9s}:  full r={rf:+.2f}   cohort r={rc:+.2f}")

    # --- Chart ---
    fed_90stock = _hhdc_auto("Page 12 Data", 3, "fed_90stock")
    fig, (axA, axB, axC) = plt.subplots(3, 1, figsize=(13, 12), sharex=True,
                                        gridspec_kw={"height_ratios": [3, 3, 2]})
    fig.suptitle(f"Fixed-seasoning-cohort index (ages {lo}-{hi} mo)  vs  full pool  vs  NY Fed",
                 fontsize=14, fontweight="bold")
    xlo, xhi = pd.Timestamp("2017-01-01"), pd.Timestamp("2026-09-30")

    # Panel A: net loss (the candidate absolute headline) — cohort vs full.
    axA.plot(idx_full.index, idx_full["net_loss_annl"] * 100, color="grey", lw=1.6,
             ls="--", label="Full pool: net loss (annualized %)")
    axA.plot(idx_coh.index, idx_coh["net_loss_annl"] * 100, color="crimson", lw=2.6,
             label=f"COHORT ({lo}-{hi}mo): net loss (annualized %)")
    axA.axvspan(COVID0, COVID1, color="orange", alpha=0.12)
    axA.set_ylabel("Net loss\n(annualized %)")
    axA.set_title("Candidate absolute headline = net annualized loss. Cohort removes the "
                  "young-pool dilution (esp. the 2020-21 dip).", fontsize=9)
    axA.legend(loc="upper left", fontsize=8)

    # Panel B: 30+ DPD cohort vs full vs Fed all-auto 90+ stock.
    axB.plot(idx_full.index, idx_full["delq30plus"] * 100, color="grey", lw=1.6,
             ls="--", label="Full pool: 30+ DPD (%)")
    axB.plot(idx_coh.index, idx_coh["delq30plus"] * 100, color="crimson", lw=2.6,
             label=f"COHORT: 30+ DPD (%)")
    b2 = axB.twinx()
    b2.plot(fed_90stock.index, fed_90stock.values, color="green", lw=1.6, ls="-.",
            label="Fed: all-auto 90+ stock (%)")
    b2.set_ylabel("Fed all-auto 90+ (%)", color="green")
    axB.axvspan(COVID0, COVID1, color="orange", alpha=0.12)
    axB.set_ylabel("30+ DPD (% of balance)")
    axB.legend(loc="upper left", fontsize=8)
    b2.legend(loc="lower right", fontsize=8)

    # Panel C: the payoff — average pool age, full vs cohort.
    axC.plot(age_full.index, age_full.values, color="grey", lw=1.6, ls="--",
             label=f"Full pool avg age (std {age_full.std():.1f}mo)")
    axC.plot(age_coh.index, age_coh.values, color="purple", lw=2.2,
             label=f"COHORT avg age (std {age_coh.std():.1f}mo) — flatter = goal")
    axC.axhspan(lo, hi, color="purple", alpha=0.06)
    axC.axvspan(COVID0, COVID1, color="orange", alpha=0.12)
    axC.set_ylabel("Avg pool age\n(months)")
    axC.set_title("The payoff: cohort holds average seasoning ~flat, so level moves "
                  "reflect credit, not aging", fontsize=9)
    axC.legend(loc="upper left", fontsize=8)
    axC.xaxis.set_major_locator(YearLocator())
    axC.xaxis.set_major_formatter(DateFormatter("%Y"))
    axC.set_xlim(xlo, xhi)

    fig.tight_layout()
    out = ROOT / "Heatmaps" / "cohort_index.png"
    fig.savefig(out, dpi=170)
    plt.close(fig)
    print(f"\nSaved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
