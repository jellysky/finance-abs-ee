"""Absolute-units comparison: our subprime metrics (native %) vs NY Fed benchmarks,
plus a pool-seasoning diagnostic that bears on using an absolute headline index.

Unlike the standardized stress index, this plots the index's *raw* pooled
components in their natural units (% of balance, annualized %), against the Fed
auto indicators. A third panel shows balance-weighted pool seasoning and the
constituent count — the composition effect that an absolute headline would have
to contend with.

Run: ``python plot_absolute_vs_fed.py``  ->  Heatmaps/absolute_vs_fed.png
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import fed_compare as fc
from plot_index_vs_fed import _hhdc_auto

ROOT = Path(__file__).resolve().parent
COVID0, COVID1 = pd.Timestamp("2020-04-01"), pd.Timestamp("2020-12-31")


def _ann_from_monthly(s: pd.Series) -> pd.Series:
    return (1.0 - (1.0 - s) ** 12) * 100.0


def _ann_from_quarterly(s: pd.Series) -> pd.Series:
    return (1.0 - (1.0 - s / 100.0) ** 4) * 100.0


def pool_seasoning() -> pd.DataFrame:
    """Balance-weighted average months-since-issuance and constituent count/month."""
    tm = pd.read_csv(ROOT / "csv" / "trust_metrics.csv", parse_dates=["month"])
    u = pd.read_csv(ROOT / "csv" / "universe.csv", parse_dates=["first_month"])
    issue = dict(zip(u["securitization_key"], u["first_month"]))
    tm["issue"] = tm["securitization_key"].map(issue)
    tm = tm.dropna(subset=["issue"])
    tm["age"] = ((tm["month"].dt.year - tm["issue"].dt.year) * 12
                 + (tm["month"].dt.month - tm["issue"].dt.month))
    w = tm["pool_beg_balance"].fillna(0.0)
    tm["_aw"] = tm["age"] * w
    g = tm.groupby("month")
    out = pd.DataFrame({
        "avg_age": g["_aw"].sum() / g.apply(lambda d: d["pool_beg_balance"].sum()),
        "n_trusts": g["securitization_key"].nunique(),
    })
    return out


def main() -> int:
    import matplotlib.pyplot as plt
    from matplotlib.dates import YearLocator, DateFormatter

    idx = fc.load_our_index().set_index("month").sort_index()
    seas = pool_seasoning()

    # Fed series (monthly-plotted at quarter-end points).
    fed_sub = fc.load_fed_subprime_below620()
    fed_sub.index = fed_sub.index.to_timestamp(how="end").normalize()
    fed_sub_ann = _ann_from_quarterly(fed_sub)
    fed_90flow = fc.load_fed_auto_90plus()
    fed_90flow.index = fed_90flow.index.to_timestamp(how="end").normalize()
    fed_30flow = _hhdc_auto("Page 13 Data", 1, "fed_30flow")     # all-auto 30+ flow (annl %)
    fed_90stock = _hhdc_auto("Page 12 Data", 3, "fed_90stock")   # all-auto % bal 90+ (stock %)

    fig, (axA, axB, axC) = plt.subplots(3, 1, figsize=(13, 12), sharex=True,
                                        gridspec_kw={"height_ratios": [3, 3, 2]})
    fig.suptitle("Subprime Auto Index in ABSOLUTE units  vs  NY Fed benchmarks",
                 fontsize=14, fontweight="bold")
    xlo, xhi = pd.Timestamp("2017-01-01"), pd.Timestamp("2026-09-30")

    # ---- Panel A: delinquency STOCK (% of balance) ----
    axA.plot(idx.index, idx["delq30plus"] * 100, color="crimson", lw=2.4,
             label="OURS: subprime 30+ DPD (% of balance)")
    axA.plot(idx.index, idx["delq60plus"] * 100, color="darkorange", lw=1.8,
             label="OURS: subprime 60+ DPD (% of balance)")
    axA.set_ylabel("Our subprime DPD\n(% of balance)")
    axA.set_ylim(0, 25)
    a2 = axA.twinx()
    a2.plot(fed_90stock.index, fed_90stock.values, color="green", lw=1.8, ls="-.",
            label="Fed: all-auto 90+ balance (% stock)")
    a2.set_ylabel("Fed all-auto 90+\n(% of balance)", color="green")
    a2.set_ylim(0, 3.2)
    axA.axvspan(COVID0, COVID1, color="orange", alpha=0.12)
    axA.set_title("Delinquency STOCK — note the ~8x level gap (subprime pool vs "
                  "all-auto) and the 2020 dip driven by young deals entering", fontsize=9)
    axA.legend(loc="upper left", fontsize=8)
    a2.legend(loc="lower right", fontsize=8)

    # ---- Panel B: annualized FLOW / loss ----
    axB.plot(idx.index, _ann_from_monthly(idx["roll_c_to_30"]), color="crimson",
             lw=2.2, label="OURS: Current->30+ roll (annualized %)")
    axB.plot(idx.index, idx["net_loss_annl"] * 100, color="#7a0000", lw=2.0, ls="-",
             label="OURS: net loss (annualized %)  [no Fed analogue]")
    axB.set_ylabel("Our flow / loss\n(annualized %)")
    b2 = axB.twinx()
    b2.plot(fed_sub_ann.index, fed_sub_ann.values, color="navy", lw=1.8, ls="--",
            marker="s", ms=3, label="Fed: subprime <620 30+ flow (annualized %)")
    b2.plot(fed_30flow.index, fed_30flow.values, color="teal", lw=1.4, ls=":",
            label="Fed: all-auto 30+ flow (annualized %)")
    b2.plot(fed_90flow.index, fed_90flow.values, color="green", lw=1.4, ls="-.",
            label="Fed: all-auto 90+ transition (annualized %)")
    b2.set_ylabel("Fed flows (annualized %)", color="navy")
    axB.axvspan(COVID0, COVID1, color="orange", alpha=0.12)
    axB.set_title("Flow into delinquency & net loss — annualized levels", fontsize=9)
    axB.legend(loc="upper left", fontsize=8)
    b2.legend(loc="upper right", fontsize=8)

    # ---- Panel C: composition / seasoning ----
    axC.plot(seas.index, seas["avg_age"], color="purple", lw=2,
             label="Balance-wtd pool seasoning (months since issuance)")
    axC.set_ylabel("Avg pool age\n(months)", color="purple")
    c2 = axC.twinx()
    c2.step(seas.index, seas["n_trusts"], color="grey", lw=1.4, where="mid",
            label="# constituent trusts")
    c2.set_ylabel("# trusts", color="grey")
    axC.axvspan(COVID0, COVID1, color="orange", alpha=0.12)
    axC.set_title("Pool composition — the absolute level co-moves with seasoning "
                  "& constituent turnover (the headline-feasibility caveat)", fontsize=9)
    axC.legend(loc="upper left", fontsize=8)
    c2.legend(loc="upper right", fontsize=8)
    axC.xaxis.set_major_locator(YearLocator())
    axC.xaxis.set_major_formatter(DateFormatter("%Y"))
    axC.set_xlim(xlo, xhi)

    fig.tight_layout()
    out = ROOT / "Heatmaps" / "absolute_vs_fed.png"
    fig.savefig(out, dpi=170)
    plt.close(fig)
    print(f"Saved -> {out}")

    # A couple of diagnostic numbers for the write-up.
    print("\nSeasoning vs absolute 30+ DPD at key months:")
    for m in ["2020-11-01", "2021-06-01", "2022-12-01", "2024-12-01", "2026-03-01"]:
        mt = pd.Timestamp(m)
        if mt in idx.index and mt in seas.index:
            print(f"  {m}: 30+={idx.loc[mt,'delq30plus']*100:5.1f}%  "
                  f"avg_age={seas.loc[mt,'avg_age']:4.1f}mo  "
                  f"n_trusts={int(seas.loc[mt,'n_trusts'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
