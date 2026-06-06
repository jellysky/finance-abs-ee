"""Per-trust x month constituent metrics for the subprime auto credit index.

Consumes the enriched auto-loan frame (already filtered to the qualifying
universe by ``universe.apply_universe``) and produces a tidy
``(securitizationKey, month)``-indexed table of the core credit-performance
metrics, plus the underlying numerator / denominator dollar amounts so the
index layer can pool across trusts *exactly* at the loan level (sum the parts,
then divide) rather than averaging ratios.

Metrics (resolved design, 2026-06-05 — see ``Analysis/Subprime Auto Index Plan.md``):

  * ``delq30plus`` — balance-weighted share 30+ DPD (leading)
  * ``delq60plus`` — balance-weighted share 60+ DPD (industry headline)
  * ``roll_c_to_30`` — flow rate Current -> 30+ DPD next month (most leading)
  * ``net_loss_annl`` — annualized net loss rate (coincident)
  * ``recovery_rate`` — recoveries / charge-offs (severity / LGD direction)

``monthsDelinquent`` encoding (from ``autoLoanParser``): 0=current, 1=30d,
2=60d, 3=90d, 4=120d+, 5=charge-off, 6=paid-in-full.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger("absee.subprime.metrics")

# monthsDelinquent state codes.
MD_CURRENT = 0
MD_30 = 1
MD_60 = 2
MD_CHARGEOFF = 5
MD_PIF = 6
# Delinquent-but-not-resolved states (exclude charge-off / paid-in-full).
_DELQ_STATES = (MD_30, MD_60, 3, 4)


def _month_start(s: pd.Series) -> pd.Series:
    dt = pd.to_datetime(s, errors="coerce")
    return dt.dt.to_period("M").dt.to_timestamp()


def _roll_current_to_30(df: pd.DataFrame) -> pd.DataFrame:
    """Balance-weighted Current -> 30+ DPD roll, per (trust, month).

    For each loan that is Current (md==0) in month M with end balance B, look up
    its delinquency state in month M+1. The denominator is B summed over loans
    that are observable next month; the numerator is B summed over those that
    are 30+ DPD (and not charged-off / paid-off) next month.

    Returns a frame indexed by (securitizationKey, month) with
    ``current_balance`` (denominator) and ``roll_30_balance`` (numerator).
    """
    cols = ["securitizationKey", "assetNumber", "_month", "monthsDelinquent",
            "reportingPeriodActualEndBalanceAmount"]
    if not all(c in df.columns for c in cols):
        return pd.DataFrame(columns=["current_balance", "roll_30_balance"])

    base = df[cols].copy()
    # Next month's state, aligned by shifting the lookup frame back one month.
    nxt = base[["securitizationKey", "assetNumber", "_month", "monthsDelinquent"]].rename(
        columns={"monthsDelinquent": "_md_next"}
    )
    nxt["_month"] = nxt["_month"] - pd.offsets.MonthBegin(1)

    cur = base.loc[base["monthsDelinquent"] == MD_CURRENT].merge(
        nxt, on=["securitizationKey", "assetNumber", "_month"], how="inner"
    )
    if cur.empty:
        return pd.DataFrame(columns=["current_balance", "roll_30_balance"])

    bal = cur["reportingPeriodActualEndBalanceAmount"]
    rolled = cur["_md_next"].isin(_DELQ_STATES)
    cur = cur.assign(current_balance=bal, roll_30_balance=bal.where(rolled, 0.0))
    return cur.groupby(["securitizationKey", "_month"])[
        ["current_balance", "roll_30_balance"]
    ].sum()


def trust_month_metrics(dtPmts: pd.DataFrame) -> pd.DataFrame:
    """Compute per-trust, per-month constituent metrics + their components.

    Args:
        dtPmts: enriched auto-loan frame, already universe-filtered.

    Returns:
        DataFrame indexed by ``(securitizationKey, month)`` with the dollar
        component columns (``*_balance``, ``net_losses``, ``charge_offs``,
        ``recoveries``, ``pool_beg_balance``, ``pool_end_balance``) and the
        derived ratio metrics (``delq30plus``, ``delq60plus``, ``roll_c_to_30``,
        ``net_loss_annl``, ``recovery_rate``). ``month`` is a month-start
        timestamp.
    """
    required = ["securitizationKey", "reportingPeriodBeginningDate",
                "monthsDelinquent", "reportingPeriodActualEndBalanceAmount"]
    missing = [c for c in required if c not in dtPmts.columns]
    if missing:
        raise ValueError(f"trust_month_metrics missing required columns: {missing}")

    df = dtPmts.copy()
    df["_month"] = _month_start(df["reportingPeriodBeginningDate"])

    end_bal = df["reportingPeriodActualEndBalanceAmount"]
    md = df["monthsDelinquent"]
    grp = df.groupby(["securitizationKey", "_month"])

    out = pd.DataFrame(index=grp.size().index)
    out["pool_end_balance"] = grp["reportingPeriodActualEndBalanceAmount"].sum()
    if "reportingPeriodBeginningLoanBalanceAmount" in df.columns:
        out["pool_beg_balance"] = grp["reportingPeriodBeginningLoanBalanceAmount"].sum()
    else:
        out["pool_beg_balance"] = np.nan

    # Delinquency numerators (balance-weighted, end-of-period balance).
    df["_delq30_bal"] = end_bal.where(md.isin(_DELQ_STATES), 0.0)
    df["_delq60_bal"] = end_bal.where(md.isin((MD_60, 3, 4)), 0.0)
    # Denominator for delinquency shares: performing + delinquent balance, i.e.
    # everything not charged-off / paid-in-full this month.
    df["_active_bal"] = end_bal.where(~md.isin((MD_CHARGEOFF, MD_PIF)), 0.0)
    grp2 = df.groupby(["securitizationKey", "_month"])
    out["delq30_balance"] = grp2["_delq30_bal"].sum()
    out["delq60_balance"] = grp2["_delq60_bal"].sum()
    out["active_balance"] = grp2["_active_bal"].sum()

    # Loss / recovery dollars.
    out["net_losses"] = grp["netLosses"].sum() if "netLosses" in df.columns else np.nan
    out["charge_offs"] = (grp["chargedoffPrincipalAmount"].sum()
                          if "chargedoffPrincipalAmount" in df.columns else np.nan)
    out["recoveries"] = (grp["recoveredAmount"].sum()
                         if "recoveredAmount" in df.columns else np.nan)

    # Roll rate components (their own observable denominator).
    roll = _roll_current_to_30(df)
    out = out.join(roll, how="left")

    # --- Derived ratios -----------------------------------------------------
    out["delq30plus"] = out["delq30_balance"] / out["active_balance"].replace(0, np.nan)
    out["delq60plus"] = out["delq60_balance"] / out["active_balance"].replace(0, np.nan)
    out["roll_c_to_30"] = out["roll_30_balance"] / out["current_balance"].replace(0, np.nan)
    out["net_loss_annl"] = 12.0 * out["net_losses"] / out["pool_beg_balance"].replace(0, np.nan)
    out["recovery_rate"] = out["recoveries"] / out["charge_offs"].replace(0, np.nan)

    out.index = out.index.set_names(["securitizationKey", "month"])
    log.info("trust_month_metrics: %d trust-months across %d trust(s)",
             len(out), out.index.get_level_values(0).nunique())
    return out.sort_index()
