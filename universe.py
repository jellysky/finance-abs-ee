"""Derive the qualifying subprime-auto-loan trust universe from ABS-EE data.

Consumes the enriched per-loan / per-month DataFrame produced by
``autoLoanParser.clean_ald_files`` followed by ``append_calc_fields`` (the same
contract the reporting layer in ``main.py`` consumes). Produces a one-row-per
securitization table describing whether each trust qualifies for the subprime
auto credit index and over which months it is "live".

The universe is defined by a *rule*, not a hand-picked list, so it self-updates
as new subprime trusts file ABS-EE:

  * auto loans only (caller passes a loan-shaped frame),
  * WAVG issuance ``consumerCreditScore`` below ``fico_cutoff`` (default 640,
    the conventional subprime line),
  * original pool larger than ``min_pool_size`` (drop small idiosyncratic deals),
  * a per-trust ``exited`` month once the pool amortizes below
    ``exit_fraction`` of its original size (kills surviving-bad-loan selection).

Resolved design decisions (2026-06-05): FICO cutoff < 640; subprime auto loans
only. See ``Analysis/Subprime Auto Index Plan.md``.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger("absee.subprime.universe")

# Defaults — overridable per call so the cutoff/size filters stay parameters.
DEFAULT_FICO_CUTOFF = 640.0
DEFAULT_MIN_POOL_SIZE = 200_000_000.0
DEFAULT_EXIT_FRACTION = 0.10


def _month_start(s: pd.Series) -> pd.Series:
    """Normalize a datetime series to first-of-month timestamps."""
    dt = pd.to_datetime(s, errors="coerce")
    return dt.dt.to_period("M").dt.to_timestamp()


def _issuance_snapshot(sec: pd.DataFrame) -> pd.DataFrame:
    """Rows for a single trust at its earliest observed reporting month.

    Prefers the cutoff snapshot (``monthsFromCutoffDate == 0``); falls back to
    the earliest ``reportingPeriodBeginningDate`` when the cutoff month was
    never downloaded.
    """
    if "monthsFromCutoffDate" in sec.columns and (sec["monthsFromCutoffDate"] == 0).any():
        return sec.loc[sec["monthsFromCutoffDate"] == 0]
    if "reportingPeriodBeginningDate" in sec.columns:
        months = _month_start(sec["reportingPeriodBeginningDate"])
        first = months.min()
        return sec.loc[months == first]
    return sec


def _wavg_issuance_fico(snapshot: pd.DataFrame) -> float:
    """Balance-weighted issuance consumer FICO over a trust's loans.

    Weights by ``originalLoanAmount`` (issuance balance), restricted to rows
    with a valid consumer score so commercial / unscored obligors don't drag
    the average. Returns NaN if no scored balance exists.
    """
    if "consumerCreditScore" not in snapshot.columns:
        return float("nan")
    score = snapshot["consumerCreditScore"]
    weight = snapshot.get("originalLoanAmount")
    if weight is None:
        weight = pd.Series(1.0, index=snapshot.index)
    valid = score.notna() & weight.notna() & (weight > 0)
    w = float(weight[valid].sum())
    if w == 0:
        return float("nan")
    return float((score[valid] * weight[valid]).sum()) / w


def _original_pool(snapshot: pd.DataFrame) -> float:
    """Original pool balance for a trust.

    Uses ``beginningBalanceAtCutoffDate`` when present (the per-loan issuance
    balance merged on by the parser), else the cutoff-month beginning balance,
    else summed ``originalLoanAmount``.
    """
    for col in ("beginningBalanceAtCutoffDate",
                "reportingPeriodBeginningLoanBalanceAmount",
                "originalLoanAmount"):
        if col in snapshot.columns:
            total = float(pd.to_numeric(snapshot[col], errors="coerce").sum())
            if total > 0:
                return total
    return float("nan")


def _exit_month(sec: pd.DataFrame, original_pool: float, exit_fraction: float):
    """First month a trust's outstanding balance falls below the exit floor.

    Returns the month-start timestamp of the first month where the trust's
    end-of-period balance drops under ``exit_fraction * original_pool``, or
    ``NaT`` if it never does within the observed window.
    """
    need = ("reportingPeriodActualEndBalanceAmount", "reportingPeriodBeginningDate")
    if not all(c in sec.columns for c in need) or not np.isfinite(original_pool):
        return pd.NaT
    months = _month_start(sec["reportingPeriodBeginningDate"])
    bal = pd.to_numeric(sec["reportingPeriodActualEndBalanceAmount"], errors="coerce")
    by_month = bal.groupby(months).sum().sort_index()
    floor = exit_fraction * original_pool
    below = by_month[by_month < floor]
    return below.index[0] if not below.empty else pd.NaT


def build_universe(
    dtPmts: pd.DataFrame,
    *,
    fico_cutoff: float = DEFAULT_FICO_CUTOFF,
    min_pool_size: float = DEFAULT_MIN_POOL_SIZE,
    exit_fraction: float = DEFAULT_EXIT_FRACTION,
) -> pd.DataFrame:
    """Classify every securitization in ``dtPmts`` for the subprime universe.

    Args:
        dtPmts: enriched auto-loan payments frame (loan-level, all trusts).
        fico_cutoff: WAVG issuance consumer FICO must be strictly below this.
        min_pool_size: original pool must exceed this (dollars).
        exit_fraction: trust is "exited" once balance < this * original pool.

    Returns:
        One row per ``securitizationKey`` with columns: ``shelf``, ``wavg_fico``,
        ``original_pool``, ``first_month``, ``last_month``, ``exited``,
        ``n_loans``, ``qualifies`` (bool), and ``reason`` (why it failed, if so).
        Indexed by ``securitizationKey`` and sorted by shelf.
    """
    if "securitizationKey" not in dtPmts.columns:
        raise ValueError("dtPmts has no 'securitizationKey' column")

    records: list[dict] = []
    for sec_key, sec in dtPmts.groupby("securitizationKey", sort=False):
        snapshot = _issuance_snapshot(sec)
        wavg_fico = _wavg_issuance_fico(snapshot)
        original_pool = _original_pool(snapshot)

        if "reportingPeriodBeginningDate" in sec.columns:
            months = _month_start(sec["reportingPeriodBeginningDate"])
            first_month, last_month = months.min(), months.max()
        else:
            first_month = last_month = pd.NaT

        exited = _exit_month(sec, original_pool, exit_fraction)
        n_loans = int(sec["assetNumber"].nunique()) if "assetNumber" in sec.columns else len(sec)

        reasons = []
        if not (np.isfinite(wavg_fico) and wavg_fico < fico_cutoff):
            reasons.append(f"wavg_fico={wavg_fico:.0f}>={fico_cutoff:.0f}"
                           if np.isfinite(wavg_fico) else "no_consumer_fico")
        if not (np.isfinite(original_pool) and original_pool > min_pool_size):
            reasons.append(f"pool=${original_pool/1e6:.0f}M<=${min_pool_size/1e6:.0f}M"
                           if np.isfinite(original_pool) else "no_pool_size")

        records.append({
            "securitizationKey": sec_key,
            "shelf": sec["shelf"].iloc[0] if "shelf" in sec.columns else sec_key,
            "wavg_fico": wavg_fico,
            "original_pool": original_pool,
            "first_month": first_month,
            "last_month": last_month,
            "exited": exited,
            "n_loans": n_loans,
            "qualifies": not reasons,
            "reason": "" if not reasons else "; ".join(reasons),
        })
        log.info("Universe: %s  fico=%.0f  pool=$%.0fM  qualifies=%s",
                 sec_key, wavg_fico if np.isfinite(wavg_fico) else -1,
                 original_pool / 1e6 if np.isfinite(original_pool) else -1,
                 not reasons)

    out = pd.DataFrame.from_records(records).set_index("securitizationKey")
    return out.sort_values("shelf")


def apply_universe(dtPmts: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """Filter ``dtPmts`` to qualifying trusts and drop post-exit months.

    Keeps only loans whose trust ``qualifies``, and within each trust drops
    reporting months on or after that trust's ``exited`` month (to avoid the
    surviving-bad-loan selection bias of a nearly paid-down pool).
    """
    qualifying = universe.index[universe["qualifies"]]
    keep = dtPmts["securitizationKey"].isin(qualifying)
    out = dtPmts.loc[keep].copy()

    if "reportingPeriodBeginningDate" in out.columns:
        months = _month_start(out["reportingPeriodBeginningDate"])
        exited = out["securitizationKey"].map(universe["exited"])
        live = exited.isna() | (months < exited)
        out = out.loc[live]

    log.info("apply_universe: kept %d/%d rows across %d trust(s)",
             len(out), len(dtPmts), out["securitizationKey"].nunique())
    return out.reset_index(drop=True)
