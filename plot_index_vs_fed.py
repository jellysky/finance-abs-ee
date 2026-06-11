"""Clean comparison graph: our subprime auto credit index vs various NY Fed indicators.

Reuses the data loaders in ``fed_compare`` and adds the remaining auto series
from the HHDC workbook, then renders a two-panel figure:

  * Top — standardized (z-score) co-movement of our pooled subprime metrics
    against four NY Fed auto indicators. Different definitions/scales, so the
    apples-to-apples view is z-scored; this is where the validation shows.
  * Bottom — our actual calculated stress index in native units (rolling-24m
    Z composite, higher = worse), the deliverable itself.

Run: ``python plot_index_vs_fed.py``  ->  Heatmaps/index_vs_fed.png
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import fed_compare as fc

ROOT = Path(__file__).resolve().parent
HHDC = fc.HHDC
COVID0, COVID1 = pd.Timestamp("2020-04-01"), pd.Timestamp("2020-12-31")


def _hhdc_auto(page: str, auto_idx: int, name: str) -> pd.Series:
    """Quarter-indexed AUTO column from an HHDC '<label>:Qn' data page."""
    df = pd.read_excel(HHDC, page, header=None)
    out = {}
    for i in range(5, len(df)):
        label = str(df.iloc[i, 0]).strip()
        if ":" not in label:
            continue
        yy, qq = label.split(":")
        val = pd.to_numeric(df.iloc[i, auto_idx], errors="coerce")
        if pd.notna(val):
            out[pd.Period(f"20{yy}{qq}", freq="Q")] = float(val)
    s = pd.Series(out, name=name).sort_index()
    s.index = s.index.to_timestamp(how="end").normalize()
    return s


def _z(s: pd.Series, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.Series:
    """Standardize over a common window (so scale/centre are comparable)."""
    w = s[(s.index >= lo) & (s.index <= hi)]
    if w.std() == 0 or w.empty:
        return s - s.mean()
    return (s - w.mean()) / w.std()


def main() -> int:
    import matplotlib.pyplot as plt
    from matplotlib.dates import YearLocator, DateFormatter

    # --- Our index (monthly -> quarterly) ---
    qm = fc.quarterly_metrics(fc.load_our_index())
    qm.index = qm.index.to_timestamp(how="end").normalize()
    our_30 = (qm["delq30plus"] * 100).rename("our_30")
    our_nl = (qm["net_loss_annl"] * 100).rename("our_nl")
    stress = qm["stress_index"].rename("stress")

    # --- Fed indicators ---
    fed_sub = fc.load_fed_subprime_below620()                 # <620, 30+ flow, quarterly %
    fed_sub.index = fed_sub.index.to_timestamp(how="end").normalize()
    fed_90flow = fc.load_fed_auto_90plus()                    # all-auto 90+ transition (annl %)
    fed_90flow.index = fed_90flow.index.to_timestamp(how="end").normalize()
    fed_30flow = _hhdc_auto("Page 13 Data", 1, "fed_30flow")  # all-auto 30+ early flow (annl %)
    fed_90stock = _hhdc_auto("Page 12 Data", 3, "fed_90stock")  # all-auto % bal 90+ (stock %)

    # Common standardization window: where we have >=4 trusts AND Fed <620 exists.
    lo, hi = pd.Timestamp("2021-06-30"), pd.Timestamp("2024-12-31")

    fig, (axT, axB) = plt.subplots(2, 1, figsize=(13, 10), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 2]})
    fig.suptitle("Subprime Auto Credit Index  vs  NY Fed auto indicators",
                 fontsize=14, fontweight="bold")

    # ---- Top: standardized co-movement ----
    series = [
        (_z(our_30, lo, hi),     "OUR index — 30+ DPD stock",            "crimson",   "-",  2.6),
        (_z(our_nl, lo, hi),     "OUR index — net loss (annualized)",    "#b30000",   "-",  1.8),
        (_z(fed_sub, lo, hi),    "Fed: subprime <620, 30+ flow (LSE)",   "navy",      "--", 1.8),
        (_z(fed_90flow, lo, hi), "Fed: all-auto 90+ transition (HHDC)",  "green",     "-.", 1.6),
        (_z(fed_30flow, lo, hi), "Fed: all-auto 30+ early flow (HHDC)",  "teal",      ":",  1.6),
        (_z(fed_90stock, lo, hi),"Fed: all-auto % bal 90+ stock (HHDC)", "darkorange","--", 1.4),
    ]
    for s, label, color, ls, lw in series:
        axT.plot(s.index, s.values, label=label, color=color, ls=ls, lw=lw)
    axT.axvspan(COVID0, COVID1, color="orange", alpha=0.12)
    axT.axhline(0, color="grey", lw=0.7, ls=":")
    axT.set_ylabel("Standardized (z-score)\nhigher = worse")
    axT.set_title("Co-movement — all series z-scored over the 2021Q2–2024Q4 overlap "
                  "(definitions/scales differ; shape & timing are the signal)",
                  fontsize=9)
    axT.legend(loc="upper left", fontsize=8, ncol=2, framealpha=0.9)
    axT.set_xlim(pd.Timestamp("2017-01-01"), pd.Timestamp("2026-09-30"))

    # ---- Bottom: our actual index in native units ----
    axB.plot(stress.index, stress.values, color="crimson", lw=2.4,
             label="OUR calculated stress index (rolling-24m Z composite)")
    axB.fill_between(stress.index, 0, stress.values,
                     where=stress.values >= 0, color="crimson", alpha=0.12)
    axB.fill_between(stress.index, 0, stress.values,
                     where=stress.values < 0, color="steelblue", alpha=0.12)
    axB.axhline(0, color="grey", lw=0.8, ls=":")
    axB.axvspan(COVID0, COVID1, color="orange", alpha=0.12, label="COVID window (flagged)")
    axB.set_ylabel("Stress (sigma)\nhigher = worse")
    axB.set_title("Our deliverable index, native units — ~0 in normal periods, "
                  "spikes on deterioration", fontsize=9)
    axB.legend(loc="upper left", fontsize=8)
    axB.xaxis.set_major_locator(YearLocator())
    axB.xaxis.set_major_formatter(DateFormatter("%Y"))

    fig.tight_layout()
    out = ROOT / "Heatmaps" / "index_vs_fed.png"
    fig.savefig(out, dpi=170)
    plt.close(fig)
    print(f"Saved -> {out}")
    print(f"Series spans: ours {qm.index.min():%Y-%m}..{qm.index.max():%Y-%m}, "
          f"Fed<620 {fed_sub.index.min():%Y-%m}..{fed_sub.index.max():%Y-%m}, "
          f"Fed all-auto {fed_90flow.index.min():%Y-%m}..{fed_90flow.index.max():%Y-%m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
