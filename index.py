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

    # --- Economic net-yield layer ------------------------------------------
    # A cash-flow construction that needs no z-scoring: everything is already in
    # dollars on the pool. Annualized (interest collected - net losses) over the
    # average pool balance. gross_yield = interest only; net_yield = after losses.
    # Pooled the same way (sum dollar parts across trusts, then divide).
    beg, end = sums.get("pool_beg_balance"), sums.get("pool_end_balance")
    interest, net_loss = sums.get("interest_collected"), sums.get("net_losses")
    if interest is not None and beg is not None and end is not None:
        avg_bal = ((beg.fillna(end) + end.fillna(beg)) / 2.0).replace(0, np.nan)
        nl = net_loss if net_loss is not None else 0.0
        out["gross_yield_annl"] = 12.0 * interest / avg_bal
        out["net_yield_annl"] = 12.0 * (interest - nl) / avg_bal

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


def _zavg_composite(z_block: pd.DataFrame, *, inverse_vol_weight: bool) -> pd.Series:
    """Linear-blend composite: inverse-volatility-weighted (default) or equal-weight."""
    if inverse_vol_weight:
        # Weight each component by the inverse of its own Z-score volatility.
        # Z-scores are already unit-variance over their *full* baseline, so this
        # mainly down-weights components with fat realized tails over the sample.
        vol = z_block.std()
        w = (1.0 / vol.replace(0, np.nan)).fillna(0.0)
        w = w / w.sum() if w.sum() else w
        return (z_block * w).sum(axis=1, min_count=1)
    return z_block.mean(axis=1)


def _pca_composite(z_block: pd.DataFrame) -> tuple[pd.Series, dict]:
    """First-principal-component composite of the (sign-oriented) z-scores.

    Fits PCA on the complete-case rows of ``z_block`` and projects every
    complete row onto PC1 — the single linear combination explaining the most
    *common* variance across the components. Because the credit metrics are
    highly co-moving, PC1 tracks the simple average closely but lets the data,
    rather than an assumed rule, set the weights. Returns the (unscaled) PC1
    series, NaN where any component is missing, plus an info dict
    (``var_explained``, ``loadings``).

    NOTE: the eigenvectors are estimated over the *whole* sample, so PC1 is
    **not causal** — adding history can revise past values. This is the same
    property as the Fed PCA stress indexes (STLFSI/KCFSI), which are revised
    series. The default ``stress_index`` (z-score average) stays fully causal;
    this variant is offered for comparison.
    """
    out = pd.Series(np.nan, index=z_block.index, name="pc1", dtype="float64")
    info: dict = {"var_explained": float("nan"), "loadings": {}}
    cc = z_block.dropna()
    if cc.shape[0] < MIN_BASELINE or cc.shape[1] < 2:
        return out, info

    X = cc.to_numpy(dtype="float64")
    Xc = X - X.mean(axis=0)
    try:
        evals, evecs = np.linalg.eigh(np.cov(Xc, rowvar=False))
    except np.linalg.LinAlgError:
        return out, info
    order = np.argsort(evals)[::-1]
    evals, evecs = evals[order], evecs[:, order]
    vec = evecs[:, 0]
    scores = Xc @ vec

    # PC1 sign is arbitrary; orient so higher = worse by aligning with the
    # equal-weight average of the (already sign-oriented) z-scores.
    ref = X.mean(axis=1)
    if np.std(scores) and np.std(ref) and np.corrcoef(scores, ref)[0, 1] < 0:
        vec, scores = -vec, -scores

    out.loc[cc.index] = scores
    total = float(evals.sum())
    info["var_explained"] = float(evals[0] / total) if total else float("nan")
    info["loadings"] = {c: float(v) for c, v in zip(z_block.columns, vec)}
    return out, info


def build_stress_index(pooled: pd.DataFrame, *, inverse_vol_weight: bool = True,
                       method: str = "zscore") -> pd.DataFrame:
    """Build the stress layer from the pooled performance components.

    Each component is rolling-Z-scored, sign-oriented so higher = worse, then
    combined into a composite. Two aggregations are produced every call:

      * ``stress_index_zavg`` — the linear blend (inverse-volatility-weighted by
        default, ``inverse_vol_weight=False`` for equal-weight). Causal.
      * ``stress_index_pca`` — projection onto the first principal component of
        the z-scores (data-driven weights), rescaled to the ``zavg`` standard
        deviation so the two overlay on the same axis. Not causal (see
        ``_pca_composite``); for comparison.

    ``method`` selects which becomes the canonical ``stress_index`` column
    (``"zscore"`` → zavg, the default and historical behavior; ``"pca"`` → PC1).
    Both variant columns are always present so they can be plotted side by side.

    Args:
        pooled: output of ``pool_metrics``.

    Returns:
        ``pooled`` with added per-component ``z_<name>`` columns,
        ``stress_index`` (the ``method`` choice), ``stress_index_zavg``,
        ``stress_index_pca``, and a boolean ``covid_flag``.
    """
    if method not in ("zscore", "pca"):
        raise ValueError(f"method must be 'zscore' or 'pca', got {method!r}")

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
    zavg = _zavg_composite(z_block, inverse_vol_weight=inverse_vol_weight)
    pca, pca_info = _pca_composite(z_block)

    # Put PC1 on the zavg scale (match std over the overlapping months) so the
    # two series are visually comparable; otherwise PC1's amplitude is arbitrary.
    both = pd.concat([zavg.rename("a"), pca.rename("p")], axis=1).dropna()
    if len(both) > 1:
        za_std, pc_std = both["a"].std(), both["p"].std()
        if pc_std and np.isfinite(pc_std):
            pca = pca * (za_std / pc_std)

    out["stress_index_zavg"] = zavg
    out["stress_index_pca"] = pca
    out["stress_index"] = pca if method == "pca" else zavg

    months = out.index.to_series()
    out["covid_flag"] = (months >= COVID_START) & (months <= COVID_END)

    n_scored = int(out["stress_index"].notna().sum())
    loadstr = ", ".join(f"{k.replace('z_', '')}={v:+.2f}"
                        for k, v in pca_info["loadings"].items())
    log.info("build_stress_index: %d/%d months scored; method=%s; weighting=%s; "
             "PCA var-explained=%.1f%% [%s]",
             n_scored, len(out), method,
             "inverse-vol" if inverse_vol_weight else "equal",
             pca_info["var_explained"] * 100 if np.isfinite(pca_info["var_explained"]) else float("nan"),
             loadstr)
    return out


def build_index(
    dtPmts: pd.DataFrame,
    *,
    fico_cutoff: float = _universe.DEFAULT_FICO_CUTOFF,
    min_pool_size: float = _universe.DEFAULT_MIN_POOL_SIZE,
    exit_fraction: float = _universe.DEFAULT_EXIT_FRACTION,
    inverse_vol_weight: bool = True,
    method: str = "zscore",
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
    idx = build_stress_index(pooled, inverse_vol_weight=inverse_vol_weight, method=method)
    return idx, uni, tm
