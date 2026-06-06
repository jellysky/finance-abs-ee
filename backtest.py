"""Visualize and sanity-check the subprime auto credit index.

Plots the stress composite and its underlying performance components, shades
the flagged COVID accommodation window, and provides a hook to overlay an
external benchmark (NY Fed Household Debt Report subprime-auto delinquency)
for the validation step in ``Analysis/Subprime Auto Index Plan.md``.

matplotlib is lazy-imported so importing this module stays cheap and the rest
of the pipeline has no hard plotting dependency.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

import index as _index

log = logging.getLogger("absee.subprime.backtest")

ROOT = Path(__file__).resolve().parent

# Performance components to show in the lower panel (raw, pre-Z-score).
_PERF_COLS = list(_index.COMPONENTS.keys())


def plot_index(idx: pd.DataFrame, *, benchmark: pd.Series | None = None,
               save_to: str | Path | None = None):
    """Plot the stress index (top) and raw performance components (bottom).

    Args:
        idx: output of ``index.build_index`` / ``build_stress_index``.
        benchmark: optional external monthly series (e.g. NY Fed subprime-auto
            90+ delinquency) indexed by month, overlaid on the top panel on a
            secondary axis.
        save_to: if given, write the figure here instead of returning the axes.
    """
    import matplotlib.pyplot as plt  # noqa: PLC0415

    if idx.empty:
        log.warning("Empty index; nothing to plot.")
        return None

    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    fig.suptitle("Subprime Auto Credit Index  (higher = worse)")

    ax_top.plot(idx.index, idx["stress_index"], color="crimson", lw=2,
                label="Stress index (rolling-Z composite)")
    ax_top.axhline(0, color="grey", lw=0.8, ls=":")
    ax_top.set_ylabel("Stress (sigma)")
    _shade_covid(idx, ax_top)

    if benchmark is not None:
        ax_b = ax_top.twinx()
        ax_b.plot(benchmark.index, benchmark.values, color="navy", lw=1.2,
                  ls="--", label="NY Fed subprime-auto delinquency")
        ax_b.set_ylabel("NY Fed series", color="navy")
        ax_b.legend(loc="upper right", fontsize=8)
    ax_top.legend(loc="upper left", fontsize=8)

    for col in _PERF_COLS:
        if col in idx.columns:
            ax_bot.plot(idx.index, idx[col], lw=1.2, label=col)
    ax_bot.set_ylabel("Performance components")
    ax_bot.legend(loc="upper left", fontsize=8, ncol=3)
    _shade_covid(idx, ax_bot)

    fig.tight_layout()
    if save_to is not None:
        fig.savefig(save_to, dpi=200)
        plt.close(fig)
        log.info("Saved index plot to %s", save_to)
        return None
    return ax_top, ax_bot


def _shade_covid(idx: pd.DataFrame, ax) -> None:
    """Shade the flagged COVID accommodation window if present in the frame."""
    if "covid_flag" not in idx.columns or not idx["covid_flag"].any():
        return
    flagged = idx.index[idx["covid_flag"]]
    ax.axvspan(flagged.min(), flagged.max(), color="orange", alpha=0.15,
               label="COVID accommodation (flagged, kept in baseline)")


def load_nyfed_subprime_auto(path: str | Path) -> pd.Series:
    """Load a NY Fed Household Debt Report subprime-auto delinquency series.

    The NY Fed publishes the Quarterly Report on Household Debt and Credit with
    an auto-loan delinquency-flow CSV; this expects a 2-column CSV of
    ``date,value``. Returns a month-indexed Series for overlay in ``plot_index``.

    Source: https://www.newyorkfed.org/microeconomics/hhdc  (data appendix).
    """
    df = pd.read_csv(path)
    date_col, val_col = df.columns[:2]
    s = pd.Series(
        pd.to_numeric(df[val_col], errors="coerce").values,
        index=pd.to_datetime(df[date_col], errors="coerce").to_period("M").to_timestamp(),
        name="nyfed_subprime_auto",
    )
    return s.dropna().sort_index()
