"""Compare the subprime auto credit index against NY Fed benchmark series.

Two official Fed benchmarks (downloaded into Inputs/fed/):

  1. Overall auto 90+ transition — HHDC "Flow into Serious Delinquency (90+)
     by Loan Type", AUTO column (annualized % of balances), 2003Q1-2026Q1.
     All-auto (prime included), so a level floor, not a subprime match — used
     for *timing/shape*.
  2. Subprime (<620) auto delinquency transition — Liberty Street Economics
     "Breaking Down Auto Loan Performance" (Feb 2025), Chart 2 "New Delinquency
     by Origination Score", <620 column (quarterly flow into 30+ DPD, by
     credit-score-at-origination), 2017Q2-2024Q4. The closest public analogue
     to our pooled <640 securitized-subprime book.

Our index is monthly; both Fed series are quarterly, so we align on calendar
quarters. Flow metrics (roll Current->30+) are compounded to a quarterly rate;
stock/level metrics are quarter-averaged. Correlations are computed on the
overlapping quarters. Definitions differ (bucket depth, <620 vs <640, balance-
vs loan-weighted), so the signal is co-movement and timing, not level identity.

Run: ``python fed_compare.py``  ->  prints stats, writes Heatmaps/index_vs_fed.png
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
FED = ROOT / "Inputs" / "fed"
HHDC = FED / "HHD_C_Report_2026Q1.xlsx"
LSE = FED / "LSE_2025_Scally_autos-data.xlsx"


# ---------------------------------------------------------------------------
# Fed series extraction -> tidy quarter-indexed Series
# ---------------------------------------------------------------------------

def _q(period_like) -> pd.Period:
    return pd.Period(period_like, freq="Q")


def load_fed_auto_90plus() -> pd.Series:
    """Overall auto 90+ transition (annualized %), HHDC Page 14 AUTO column."""
    df = pd.read_excel(HHDC, "Page 14 Data", header=None)
    hdr = df.iloc[4].tolist()
    auto_col = hdr.index("AUTO")
    out = {}
    for i in range(5, len(df)):
        label = str(df.iloc[i, 0]).strip()
        if ":" not in label:
            continue
        yy, qq = label.split(":")  # '03:Q1'
        per = _q(f"20{yy}{qq}")
        val = pd.to_numeric(df.iloc[i, auto_col], errors="coerce")
        if pd.notna(val):
            out[per] = float(val)
    return pd.Series(out, name="fed_auto90_annl_pct").sort_index()


def load_fed_subprime_below620() -> pd.Series:
    """Subprime <620 quarterly flow into 30+ DPD (%), LSE Chart 2."""
    df = pd.read_excel(LSE, "Chart 2", header=None)
    hdr = [str(x) for x in df.iloc[7].tolist()]
    col = hdr.index("<620")
    out = {}
    for i in range(8, len(df)):
        label = str(df.iloc[i, 0]).strip()
        if not label[:6].isdigit():
            continue
        per = pd.Period(f"{label[:4]}-{label[4:6]}", freq="M").asfreq("Q")
        val = pd.to_numeric(df.iloc[i, col], errors="coerce")
        if pd.notna(val):
            out[per] = float(val) * 100.0  # fraction -> percent
    return pd.Series(out, name="fed_sub620_30plus_q_pct").sort_index()


def save_fed_csvs() -> None:
    """Persist both series as date,value CSVs (load_nyfed_subprime_auto format)."""
    for s, fn in ((load_fed_auto_90plus(), "fed_auto_90plus_transition.csv"),
                  (load_fed_subprime_below620(), "fed_subprime_below620_30plus.csv")):
        out = s.copy()
        out.index = out.index.to_timestamp(how="end").normalize()
        out.rename_axis("date").to_frame("value").to_csv(FED / fn)


# ---------------------------------------------------------------------------
# Our index -> quarterly
# ---------------------------------------------------------------------------

def load_our_index() -> pd.DataFrame:
    idx = pd.read_csv(ROOT / "csv" / "index_marks.csv", parse_dates=["month"])
    idx["q"] = idx["month"].dt.to_period("Q")
    return idx


def quarterly_metrics(idx: pd.DataFrame) -> pd.DataFrame:
    """Aggregate monthly metrics to quarters: flows compounded, levels averaged."""
    g = idx.groupby("q")
    out = pd.DataFrame(index=g.size().index)
    # Stocks / levels: mean across the quarter's months.
    for c in ["delq30plus", "delq60plus", "net_loss_annl", "recovery_rate",
              "stress_index", "n_trusts"]:
        if c in idx.columns:
            out[c] = g[c].mean()
    # Flow: monthly Current->30+ compounded into a quarterly rate.
    if "roll_c_to_30" in idx.columns:
        out["roll_c_to_30_q"] = g["roll_c_to_30"].apply(
            lambda s: 1.0 - np.prod(1.0 - s.dropna().to_numpy())
            if s.notna().any() else np.nan
        )
    return out


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _corr(a: pd.Series, b: pd.Series) -> tuple[float, float, int]:
    j = pd.concat([a, b], axis=1).dropna()
    if len(j) < 4:
        return np.nan, np.nan, len(j)
    x, y = j.iloc[:, 0], j.iloc[:, 1]
    # Spearman = Pearson on ranks (avoids a scipy dependency).
    return (float(x.corr(y)),
            float(x.rank().corr(y.rank())),
            len(j))


def main() -> int:
    save_fed_csvs()
    fed90 = load_fed_auto_90plus()
    sub = load_fed_subprime_below620()
    qm = quarterly_metrics(load_our_index())

    fed90.index = fed90.index.to_timestamp(how="end").normalize()
    sub.index = sub.index.to_timestamp(how="end").normalize()
    qm.index = qm.index.to_timestamp(how="end").normalize()

    print("=" * 74)
    print("SUBPRIME AUTO CREDIT INDEX  vs  NY FED BENCHMARKS")
    print("=" * 74)
    print(f"Our index quarters:   {qm.index.min():%Y-%m} .. {qm.index.max():%Y-%m}  "
          f"({len(qm)} quarters, {int(qm['n_trusts'].max())} trusts at peak)")
    print(f"Fed auto 90+ (all):   {fed90.index.min():%Y-%m} .. {fed90.index.max():%Y-%m}")
    print(f"Fed subprime <620:    {sub.index.min():%Y-%m} .. {sub.index.max():%Y-%m}")

    print("\n--- CORRELATIONS over overlapping quarters (Pearson / Spearman / n) ---")
    metrics = [("stress_index", "stress index (composite Z)"),
               ("roll_c_to_30_q", "roll Current->30+ (quarterly flow)"),
               ("delq30plus", "30+ DPD stock"),
               ("delq60plus", "60+ DPD stock"),
               ("net_loss_annl", "net loss (annualized)")]
    # Restrict to the breadth-robust period (>=4 constituents): early quarters
    # are one seasoning deal, whose age curve is deal-specific noise, not market.
    robust = qm[qm["n_trusts"] >= 4]
    robust_from = robust.index.min()
    for bench, blabel in [(sub, "Fed SUBPRIME <620 (30+ flow)"),
                          (fed90, "Fed ALL-AUTO 90+ transition")]:
        print(f"\n  vs {blabel}:")
        print(f"    {'metric':38s}  {'full sample':>18s}   {'>=4 trusts only':>18s}")
        for key, label in metrics:
            if key in qm.columns:
                p, sp, n = _corr(qm[key], bench)
                pr, spr, nr = _corr(robust[key], bench)
                print(f"    {label:38s}  r={p:+.2f} rho={sp:+.2f} n={n:<3d}   "
                      f"r={pr:+.2f} rho={spr:+.2f} n={nr}")
    print(f"  (>=4-trust period starts {robust_from:%Y-%m})")

    def qlab(ts):
        return f"{ts.year}Q{(ts.month - 1) // 3 + 1}"

    # Level snapshot: our compounded quarterly subprime roll vs LSE <620.
    print("\n--- LEVEL SNAPSHOT: our roll C->30+ (q, %) vs Fed <620 (q, %) ---")
    comp = pd.concat([(qm["roll_c_to_30_q"] * 100).rename("ours"),
                      sub.rename("fed_sub620")], axis=1).dropna()
    for dt, r in comp.tail(10).iterrows():
        print(f"    {qlab(dt)}   ours={r['ours']:5.2f}%   fed<620={r['fed_sub620']:5.2f}%")

    # Full aligned quarterly table of the headline series.
    print("\n--- ALIGNED QUARTERLY TABLE (headline) ---")
    tbl = pd.concat([
        qm["n_trusts"].rename("n"),
        (qm["delq30plus"] * 100).rename("our30+"),
        (qm["net_loss_annl"] * 100).rename("ourNL%"),
        qm["stress_index"].rename("stress"),
        sub.rename("fed<620"),
        fed90.rename("fedAuto90"),
    ], axis=1)
    tbl = tbl[tbl["n"].notna()]
    print(f"    {'quarter':8s} {'n':>3s} {'our30+':>7s} {'ourNL%':>7s} "
          f"{'stress':>7s} {'fed<620':>8s} {'fedAuto90':>9s}")
    for dt, r in tbl.iterrows():
        f = lambda v, w, d=1: (f"{v:{w}.{d}f}" if pd.notna(v) else " " * w)
        print(f"    {qlab(dt):8s} {int(r['n']):3d} {f(r['our30+'],7)} {f(r['ourNL%'],7)} "
              f"{f(r['stress'],7,2)} {f(r['fed<620'],8)} {f(r['fedAuto90'],9,2)}")

    _plot(qm, fed90, sub)
    print("\nSaved overlay chart -> Heatmaps/index_vs_fed.png")
    print("Saved Fed CSVs       -> Inputs/fed/fed_*.csv")
    return 0


def _plot(qm: pd.DataFrame, fed90: pd.Series, sub: pd.Series) -> None:
    import matplotlib.pyplot as plt  # noqa: PLC0415
    from matplotlib.dates import YearLocator

    covid0, covid1 = pd.Timestamp("2020-04-01"), pd.Timestamp("2020-12-31")
    fig, axes = plt.subplots(3, 1, figsize=(12, 11), sharex=True)
    fig.suptitle("Subprime Auto Credit Index vs NY Fed benchmarks  (higher = worse)",
                 fontsize=13)

    # Panel 1: stress index (Z) vs both Fed series, each standardized for shape.
    ax = axes[0]
    ax.plot(qm.index, qm["stress_index"], color="crimson", lw=2.2,
            label="Our stress index (rolling-Z composite)")
    ax.axhline(0, color="grey", lw=0.8, ls=":")
    ax.set_ylabel("Stress (sigma)")
    axb = ax.twinx()
    z = lambda s: (s - s.mean()) / s.std()
    axb.plot(sub.index, z(sub), color="navy", lw=1.4, ls="--",
             label="Fed <620 30+ flow (standardized)")
    axb.plot(fed90.index, z(fed90), color="green", lw=1.2, ls="-.",
             label="Fed all-auto 90+ (standardized)")
    axb.set_ylabel("Fed series (z)")
    ax.axvspan(covid0, covid1, color="orange", alpha=0.15)
    ax.legend(loc="upper left", fontsize=8)
    axb.legend(loc="lower right", fontsize=8)

    # Panel 2: most apples-to-apples levels — our quarterly roll vs Fed <620.
    ax = axes[1]
    ax.plot(qm.index, qm["roll_c_to_30_q"] * 100, color="crimson", lw=2,
            marker="o", ms=3, label="Our roll Current->30+ (quarterly %, balance-wtd)")
    ax.plot(sub.index, sub.values, color="navy", lw=1.6, ls="--",
            marker="s", ms=3, label="Fed <620 new 30+ delinquency (quarterly %)")
    ax.axvspan(covid0, covid1, color="orange", alpha=0.15)
    ax.set_ylabel("Quarterly flow into 30+ (%)")
    ax.legend(loc="upper left", fontsize=8)

    # Panel 3: our stock delinquency vs Fed all-auto 90+ transition (annualized).
    ax = axes[2]
    ax.plot(qm.index, qm["delq30plus"] * 100, color="crimson", lw=2,
            label="Our 30+ DPD stock (%)")
    ax.plot(qm.index, qm["delq60plus"] * 100, color="darkorange", lw=1.5,
            label="Our 60+ DPD stock (%)")
    axb = ax.twinx()
    axb.plot(fed90.index, fed90.values, color="green", lw=1.6, ls="-.",
             label="Fed all-auto 90+ transition (annualized %)")
    axb.set_ylabel("Fed all-auto 90+ (%)", color="green")
    ax.axvspan(covid0, covid1, color="orange", alpha=0.15)
    ax.set_ylabel("Our DPD stock (%)")
    ax.xaxis.set_major_locator(YearLocator())
    ax.legend(loc="upper left", fontsize=8)
    axb.legend(loc="lower right", fontsize=8)

    fig.tight_layout()
    fig.savefig(ROOT / "Heatmaps" / "index_vs_fed.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
