"""Construct the subprime auto credit index from per-trust monthly metrics.

Two layers, built in order so each can be debugged independently:

  1. **Performance layer** — a loan-level *pooled*, balance-weighted monthly
     composite. Trust boundaries are dissolved by summing each metric's dollar
     numerator and denominator across all live trusts in the month, then
     dividing. This is exactly the metric you'd get by recomputing on the
     pooled loan universe, and it avoids the static-pool seasoning bias of
     equal-weighting deals of different ages.

  2. **Stress layer** — a normalized deterioration signal. Each pooled
     component is converted to a rolling-24-month Z-score (capped at +/-3 sigma),
     oriented so *higher = worse*, and combined into one composite. The output
     hovers near 0 in normal periods and spikes positive when subprime auto is
     deteriorating (VIX-like).

Resolved design decisions (2026-06-05): orientation higher = worse; COVID
window (Apr-Dec 2020) flagged but kept in the baseline; monthly cadence.
See ``Analysis/Subprime Auto Index Plan.md``.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import metrics as _metrics
import universe as _universe

log = logging.getLogger("absee.subprime.index")

# Components of the composite. Each maps a pooled ratio to (numerator,
# denominator) dollar columns and a sign: +1 if a higher ratio means *worse*
# credit, -1 if higher means *better* (inverted so the stress index stays
# "higher = worse").
COMPONENTS: dict[str, dict] = {
    "delq30plus":    {"num": "delq30_balance", "den": "active_balance",   "sign": +1},
    "delq60plus":    {"num": "delq60_balance", "den": "active_balance",   "sign": +1},
    "roll_c_to_30":  {"num": "roll_30_balance", "den": "current_balance", "sign": +1},
    "net_loss_annl": {"num": "net_losses",      "den": "pool_beg_balance", "sign": +1,
                      "annualize": 12.0},
    "recovery_rate": {"num": "recoveries",      "den": "charge_offs",      "sign": -1},
}

ROLLING_WINDOW = 24      # months of baseline for the Z-score
MIN_BASELINE = 12        # require at least this many months before scoring
Z_CAP = 3.0              # clamp each component Z-score to +/- this
COVID_START = pd.Timestamp("2020-04-01")
COVID_END = pd.Timestamp("2020-12-01")


def pool_metrics(trust_metrics: pd.DataFrame) -> pd.DataFrame:
    """Pool per-trust components into one loan-level monthly time series.

    Sums each component's numerator and denominator dollar amounts across all
    trusts present in the month, then divides to get the pooled ratio. Also
    sums ``pool_end_balance`` and counts constituents for context.

    Args:
        trust_metrics: output of ``metrics.trust_month_metrics`` (indexed by
            ``(securitizationKey, month)``).

    Returns:
        DataFrame indexed by ``month`` with one column per component ratio plus
        ``pool_balance`` and ``n_trusts``.
    """
    by_month = trust_metrics.groupby(level="month")
    sums = by_month.sum(min_count=1)

    out = pd.DataFrame(index=sums.index)
    out["n_trusts"] = by_month.size()
    out["pool_balance"] = sums.get("pool_end_balance", np.nan)

    for name, spec in COMPONENTS.items():
        num = sums.get(spec["num"])
        den = sums.get(spec["den"])
        if num is None or den is None:
            out[name] = np.nan
            continue
        ratio = num / den.replace(0, np.nan)
        if "annualize" in spec:
            ratio = ratio * spec["annualize"]
        out[name] = ratio

    out.index.name = "month"
    log.info("pool_metrics: %d months, %d-%d constituents",
             len(out), int(out["n_trusts"].min()), int(out["n_trusts"].max()))
    return out.sort_index()


def _rolling_z(s: pd.Series) -> pd.Series:
    """Rolling-window Z-score: (x - rolling mean) / rolling std, capped.

    The baseline window includes the current point (causal, no look-ahead is
    introduced beyond the point itself). Uses a minimum number of observations
    so early months aren't scored off one or two data points.
    """
    mean = s.rolling(ROLLING_WINDOW, min_periods=MIN_BASELINE).mean()
    std = s.rolling(ROLLING_WINDOW, min_periods=MIN_BASELINE).std()
    z = (s - mean) / std.replace(0, np.nan)
    return z.clip(-Z_CAP, Z_CAP)


def build_stress_index(pooled: pd.DataFrame, *, inverse_vol_weight: bool = True) -> pd.DataFrame:
    """Build the stress layer from the pooled performance components.

    Each component is rolling-Z-scored, sign-oriented so higher = worse, then
    combined into a composite. By default components are weighted by inverse
    historical volatility (so a noisy metric doesn't dominate); pass
    ``inverse_vol_weight=False`` for an equal-weight blend.

    Args:
        pooled: output of ``pool_metrics``.

    Returns:
        ``pooled`` with added per-component ``z_<name>`` columns, a
        ``stress_index`` composite, and a boolean ``covid_flag``.
    """
    out = pooled.copy()
    z_cols: list[str] = []
    for name, spec in COMPONENTS.items():
        if name not in out.columns:
            continue
        z = _rolling_z(out[name]) * spec["sign"]
        zc = f"z_{name}"
        out[zc] = z
        z_cols.append(zc)

    z_block = out[z_cols]
    if inverse_vol_weight:
        # Weight each component by the inverse of its own Z-score volatility.
        # Z-scores are already unit-variance over their *full* baseline, so this
        # mainly down-weights components with fat realized tails over the sample.
        vol = z_block.std()
        w = (1.0 / vol.replace(0, np.nan)).fillna(0.0)
        w = w / w.sum() if w.sum() else w
        out["stress_index"] = (z_block * w).sum(axis=1, min_count=1)
    else:
        out["stress_index"] = z_block.mean(axis=1)

    months = out.index.to_series()
    out["covid_flag"] = (months >= COVID_START) & (months <= COVID_END)

    n_scored = int(out["stress_index"].notna().sum())
    log.info("build_stress_index: %d/%d months scored; weighting=%s",
             n_scored, len(out), "inverse-vol" if inverse_vol_weight else "equal")
    return out


def build_index(
    dtPmts: pd.DataFrame,
    *,
    fico_cutoff: float = _universe.DEFAULT_FICO_CUTOFF,
    min_pool_size: float = _universe.DEFAULT_MIN_POOL_SIZE,
    exit_fraction: float = _universe.DEFAULT_EXIT_FRACTION,
    inverse_vol_weight: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """End-to-end build from an enriched auto-loan frame to the index.

    Runs ``universe.build_universe`` + ``apply_universe``,
    ``metrics.trust_month_metrics``, ``pool_metrics`` and ``build_stress_index``.

    Returns:
        ``(index_df, universe_df, trust_metrics_df)`` where ``index_df`` carries
        the pooled performance components, per-component Z-scores, the
        ``stress_index`` composite and ``covid_flag``.
    """
    uni = _universe.build_universe(
        dtPmts, fico_cutoff=fico_cutoff,
        min_pool_size=min_pool_size, exit_fraction=exit_fraction,
    )
    filtered = _universe.apply_universe(dtPmts, uni)
    if filtered.empty:
        log.warning("No qualifying subprime trusts in input; returning empty index.")
        return pd.DataFrame(), uni, pd.DataFrame()

    tm = _metrics.trust_month_metrics(filtered)
    pooled = pool_metrics(tm)
    idx = build_stress_index(pooled, inverse_vol_weight=inverse_vol_weight)
    return idx, uni, tm
