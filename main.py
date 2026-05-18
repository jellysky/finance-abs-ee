"""ABS-EE reporting and analysis layer.

Operates on the enriched DataFrames produced by ``autoLoanParser`` (or by
``autoLeaseParser`` followed by ``autoLeaseParser.fit_reporting_model`` to
align lease column names with the loan schema). Produces:

  * ``create_summary``           — stratified roll-up across the standard cuts
  * ``create_comparison``        — one-row-per-securitization summary
  * ``create_performance``       — performance time-series by axis (vintage / age / month)
  * ``create_curves``            — life-of-deal balance curves by prime tier
  * ``create_rollrates_matrix``  — single delinquency-state transition matrix
  * ``create_rollrates_ts``      — time-series of roll-rates by axis
  * ``describe_data``            — per-securitization describe()

Optional (require matplotlib/seaborn/sklearn — lazy-imported on use):
  * ``plot_performance``, ``plot_model_outputs``
  * ``create_heatmap``, ``plot_heatmaps``, ``run_heatmaps``
"""
from __future__ import annotations

import itertools as it
import logging
from typing import Iterable

import numpy as np
import numpy_financial as npf
import pandas as pd

log = logging.getLogger("absee.reporting")


class const:
    @staticmethod
    def summaryRowHeader():
        return ['No of Loans', 'Avg Prin Bal $', 'Avg Prin Bal %', 'Avg Loan Size $',
                'BaseR / MSRP %', 'DBaseR / Sec %', 'Total TurnIn %',
                'Total Gain/Loss Sale %', 'Total Def %', 'Total PP %', 'Total Delq %',
                'Delq 1m %', 'Delq 2m %', 'Delq 3m %', 'Delq 3m+ %', 'Def %', 'PP %',
                'Inc Ver %', 'Emp Ver %', 'WAVG APR %', 'WAVG LTV %', 'WAVG Term m',
                'WAVG Age m', 'WAVG MFC m', 'WAVG PTI %', 'WAVG Cons Score',
                'WAVG Comm Score', 'Cons %', 'Comm %', 'Used %', 'New %', 'Car %',
                'Truck %', 'SUV %', '<2010 %', '<2015 %', '2015 %', '2016 %', '2017 %',
                'CA %', 'TX %', 'FL %', 'OH %', 'NJ %']

    @staticmethod
    def intNo(): return 7
    @staticmethod
    def rrCols():
        return ['Curr', '1m', '2m', '3m', '3m+', 'CO', 'PP']
    @staticmethod
    def featTolerance(): return 10
    @staticmethod
    def maxTrees(): return 200
    @staticmethod
    def maxDepth(): return None
    @staticmethod
    def maxLeaf(): return 1

    @staticmethod
    def modelFields():
        return ['gracePeriodNumber', 'obligorEmploymentVerificationCode', 'region',
                'obligorIncomeVerificationLevelCode', 'originalInterestRatePercentage',
                'originalLoanAmount', 'originalLoanTerm', 'paymentToIncomePercentage',
                'remainingTermToMaturityNumber', 'servicingAdvanceMethodCode',
                'servicingFeePercentage', 'underwritingIndicator',
                'vehicleManufacturerName', 'vehicleModelYear', 'vehicleNewUsedCode',
                'vehicleTypeCode', 'vehicleValueAmount', 'vehicleValueSourceCode',
                'consumerCreditScore', 'commercialCreditScore', 'age',
                'loanToValueRatio', 'vintage', 'primeIndicator']

    @staticmethod
    def frac(): return .2

    @staticmethod
    def badFields():
        return np.array(['commercialCreditScore', 'obligorEmploymentVerificationCode',
                         'gracePeriodNumber', 'obligorIncomeVerificationCode',
                         'servicingFeePercentage', 'vehicleManufacturerName'])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def WAVG(dtPmts: pd.DataFrame, fieldStr: str, weightStr: str) -> float:
    """Weight-average ``fieldStr`` by ``weightStr``. Returns NaN if weights sum to 0."""
    w = dtPmts[weightStr]
    total = float(w.sum())
    if total == 0:
        return float('nan')
    return float((dtPmts[fieldStr] * w).sum()) / total


def _div(num: float, den: float) -> float:
    """Safe scalar divide returning NaN on zero denominator."""
    return float(num) / float(den) if den else float('nan')


# ---------------------------------------------------------------------------
# Summary row construction
# ---------------------------------------------------------------------------

def create_summary_row(dtPmts: pd.DataFrame, rowName: str) -> pd.DataFrame | None:
    """Build a one-row summary DataFrame for the given slice. Returns None if empty."""
    if dtPmts.shape[0] == 0:
        log.warning("No rows in segmentation: %s", rowName)
        return None

    row: dict = {h: 0.0 for h in const.summaryRowHeader()}

    first_mask = dtPmts['monthsFromCutoffDate'] == 0
    first_open_bal = float(dtPmts.loc[first_mask, 'reportingPeriodBeginningLoanBalanceAmount'].sum())
    row['Total Def %'] = _div(dtPmts['chargedoffPrincipalAmount'].sum(), first_open_bal)
    row['Total PP %'] = _div(dtPmts['principalPrepaid'].sum(), first_open_bal)

    last_mask = dtPmts['summaryDate'] == 1
    dtP = dtPmts.loc[last_mask]
    n = dtP.shape[0]

    if n > 0:
        end_bal_total = float(dtP['reportingPeriodActualEndBalanceAmount'].sum())
        beg_bal_total = float(dtP['reportingPeriodBeginningLoanBalanceAmount'].sum())

        row['No of Loans'] = n
        row['Avg Prin Bal $'] = float(dtP['reportingPeriodActualEndBalanceAmount'].mean())
        row['Avg Prin Bal %'] = _div(
            float(dtP['reportingPeriodActualEndBalanceAmount'].mean()),
            float(dtP['originalLoanAmount'].mean()),
        )
        row['Avg Loan Size $'] = float(dtP['originalLoanAmount'].mean())

        end_bal_w = dtP['reportingPeriodActualEndBalanceAmount']
        row['Total Delq %'] = _div(
            ((dtP['currentDelinquencyStatus'] > 0) * end_bal_w).sum(), end_bal_total
        )
        row['Delq 1m %'] = _div(((dtP['monthsDelinquent'] == 1) * end_bal_w).sum(), end_bal_total)
        row['Delq 2m %'] = _div(((dtP['monthsDelinquent'] == 2) * end_bal_w).sum(), end_bal_total)
        row['Delq 3m %'] = _div(((dtP['monthsDelinquent'] == 3) * end_bal_w).sum(), end_bal_total)
        row['Delq 3m+ %'] = _div(((dtP['monthsDelinquent'] == 4) * end_bal_w).sum(), end_bal_total)
        row['Def %'] = _div(dtP['chargedoffPrincipalAmount'].sum(), end_bal_total)
        row['PP %'] = _div(dtP['principalPrepaid'].sum(), beg_bal_total)

        row['WAVG LTV %'] = WAVG(dtP, 'loanToValueRatio', 'originalLoanAmount')
        row['WAVG Term m'] = WAVG(dtP, 'originalLoanTerm', 'originalLoanAmount')
        row['WAVG Age m'] = WAVG(dtP, 'age', 'originalLoanAmount')
        row['WAVG MFC m'] = WAVG(dtP, 'monthsFromCutoffDate', 'originalLoanAmount') + 1
        row['WAVG PTI %'] = WAVG(dtP, 'paymentToIncomePercentage', 'originalLoanAmount')

        cons_mask = (dtP['consumerCreditScore'] > 0) & dtP['consumerCreditScore'].notna()
        comm_mask = (dtP['commercialCreditScore'] > 0) & dtP['commercialCreditScore'].notna()
        if cons_mask.any():
            row['WAVG Cons Score'] = WAVG(dtP.loc[cons_mask], 'consumerCreditScore', 'originalLoanAmount')
            row['Cons %'] = _div(int(cons_mask.sum()), n)
        if comm_mask.any():
            row['WAVG Comm Score'] = WAVG(dtP.loc[comm_mask], 'commercialCreditScore', 'originalLoanAmount')
            row['Comm %'] = _div(int(comm_mask.sum()), n)

        row['New %'] = _div((dtP['vehicleNewUsedCode'] == 1).sum(), n)
        row['Used %'] = _div((dtP['vehicleNewUsedCode'] == 2).sum(), n)

        row['Car %'] = _div((dtP['vehicleTypeCode'] == 1).sum(), n)
        row['Truck %'] = _div((dtP['vehicleTypeCode'] == 2).sum(), n)
        row['SUV %'] = _div((dtP['vehicleTypeCode'] == 3).sum(), n)

        row['<2010 %'] = _div((dtP['vehicleModelYear'] < 2010).sum(), n)
        row['<2015 %'] = _div((dtP['vehicleModelYear'] < 2015).sum(), n)
        row['2015 %'] = _div((dtP['vehicleModelYear'] == 2015).sum(), n)
        row['2016 %'] = _div((dtP['vehicleModelYear'] == 2016).sum(), n)
        row['2017 %'] = _div((dtP['vehicleModelYear'] == 2017).sum(), n)

        for st in ('CA', 'TX', 'FL', 'OH', 'NJ'):
            row[f'{st} %'] = _div((dtP['obligorGeographicLocation'] == st).sum(), n)

        if 'obligorIncomeVerificationLevelCode' in dtP.columns:
            row['Inc Ver %'] = _div((dtP['obligorIncomeVerificationLevelCode'] >= 3).sum(), n)
        if 'obligorEmploymentVerificationCode' in dtP.columns:
            row['Emp Ver %'] = _div((dtP['obligorEmploymentVerificationCode'] >= 3).sum(), n)

        if 'baseResidualValue' in dtP.columns:
            if 'terminationIndicator' in dtPmts.columns:
                row['Total TurnIn %'] = _div(
                    int(np.isin(dtPmts['terminationIndicator'], [2, 4]).sum()),
                    int(dtPmts['assetNumber'].nunique()),
                )
            if 'securitizationDiscountRate' in dtP.columns:
                row['WAVG APR %'] = WAVG(dtP, 'securitizationDiscountRate', 'originalLoanAmount')
            if 'vehicleValueAmount' in dtP.columns:
                row['BaseR / MSRP %'] = _div(
                    float(dtP['baseResidualValue'].sum()),
                    float(dtP['vehicleValueAmount'].sum()),
                )
            if all(c in dtP.columns for c in
                   ('securitizationDiscountRate', 'remainingTermToMaturityNumber',
                    'reportingPeriodBeginningLoanBalanceAmount')):
                pv_per_loan = npf.pv(
                    dtP['securitizationDiscountRate'],
                    dtP['remainingTermToMaturityNumber'],
                    0,
                    -dtP['baseResidualValue'],
                    1,
                )
                row['DBaseR / Sec %'] = _div(
                    float(np.nansum(pv_per_loan)),
                    float(dtP['reportingPeriodBeginningLoanBalanceAmount'].sum()),
                )
            if 'saleGainOrLoss' in dtPmts.columns and 'contractResidualValue' in dtPmts.columns:
                gain_mask = dtPmts['saleGainOrLoss'].abs() > 0
                if gain_mask.any():
                    row['Total Gain/Loss Sale %'] = _div(
                        float(dtPmts.loc[gain_mask, 'saleGainOrLoss'].sum()),
                        float(dtPmts.loc[gain_mask, 'contractResidualValue'].sum()),
                    )
            drop_cols = []
        else:
            row['WAVG APR %'] = WAVG(dtP, 'originalInterestRatePercentage', 'originalLoanAmount')
            drop_cols = ['BaseR / MSRP %', 'DBaseR / Sec %', 'Total TurnIn %',
                         'Total Gain/Loss Sale %']

    else:
        drop_cols = []

    dtRow = pd.DataFrame([row], index=[rowName.title()])
    if drop_cols:
        dtRow = dtRow.drop(columns=[c for c in drop_cols if c in dtRow.columns])
    return dtRow


def create_summary_strats(
    dtPmts: pd.DataFrame,
    functionStr: str,
    fieldStr: str,
    rowStr: str,
    labels: list,
    factor: int,
) -> pd.DataFrame:
    """Stratify dtPmts into N bins by fieldStr and produce one summary row per bin."""
    dtSumm = create_summary_row(dtPmts, f"{rowStr}: Overall")

    if functionStr == 'stratify':
        lo = float(dtPmts[fieldStr].min())
        hi = float(dtPmts[fieldStr].max())
        step = (hi - lo) / const.intNo()
        starts = lo + np.arange(0, const.intNo()) * step
        ends = lo + np.arange(1, const.intNo() + 1) * step
        for s, e in zip(starts, ends):
            mask = (dtPmts[fieldStr] >= s) & (dtPmts[fieldStr] < e) & dtPmts[fieldStr].notna()
            row = create_summary_row(dtPmts.loc[mask], f"{rowStr}: {s:.2f} - {e:.2f}")
            if row is not None:
                dtSumm = pd.concat([dtSumm, row], axis=0)

    elif functionStr == 'labels':
        for i, lab in enumerate(labels):
            mask = dtPmts[fieldStr] == (i + factor)
            row = create_summary_row(dtPmts.loc[mask], f"{rowStr}: {lab}")
            if row is not None:
                dtSumm = pd.concat([dtSumm, row], axis=0)

    elif functionStr == 'unique':
        for lab in np.sort(dtPmts[fieldStr].dropna().unique()):
            mask = dtPmts[fieldStr] == lab
            row = create_summary_row(dtPmts.loc[mask], f"{rowStr}: {lab}")
            if row is not None:
                dtSumm = pd.concat([dtSumm, row], axis=0)
    else:
        raise ValueError(f"Unknown functionStr: {functionStr!r}")

    log.info("Calculated stratification: %s", rowStr)
    return dtSumm


def create_summary(dtPmts: pd.DataFrame) -> pd.DataFrame:
    """Top-level summary: all standard stratifications stacked."""
    log.info("Creating summary report")
    parts = [
        create_summary_strats(dtPmts, 'stratify', 'originalLoanTerm', 'Term', [], 0),
        create_summary_strats(dtPmts, 'stratify', 'originalLoanAmount', 'Size', [], 0),
        create_summary_strats(dtPmts, 'stratify', 'nextInterestRatePercentage', 'APR', [], 0),
        create_summary_strats(dtPmts, 'stratify', 'age', 'Age', [], 0),
        create_summary_strats(dtPmts, 'stratify', 'consumerCreditScore', 'FICO', [], 0),
        create_summary_strats(dtPmts, 'stratify', 'commercialCreditScore', 'CommScore', [], 0),
        create_summary_strats(dtPmts, 'stratify', 'loanToValueRatio', 'LTV', [], 0),
        create_summary_strats(dtPmts, 'stratify', 'paymentToIncomePercentage', 'PTI', [], 0),
        create_summary_strats(dtPmts, 'labels', 'vehicleTypeCode', 'VehType',
                              ['Car', 'Truck', 'SUV'], 1),
        create_summary_strats(dtPmts, 'labels', 'vehicleNewUsedCode', 'VehCond',
                              ['New', 'Used'], 1),
        create_summary_strats(dtPmts, 'labels', 'vehicleModelYear', 'VehYear',
                              ['2012', '2013', '2014', '2015', '2016', '2017'], 2012),
        create_summary_strats(dtPmts, 'unique', 'obligorEmploymentVerificationCode', 'EmpVer', [], 0),
        create_summary_strats(dtPmts, 'unique', 'obligorIncomeVerificationLevelCode', 'IncVer', [], 0),
        create_summary_strats(dtPmts, 'unique', 'vehicleManufacturerName', 'VehManu', [], 0),
    ]
    return pd.concat(parts, axis=0)


def create_comparison(dtPmts: pd.DataFrame) -> pd.DataFrame:
    """One summary row per securitization, plus an 'All Trusts' row at top."""
    sec_keys = np.sort(dtPmts['securitizationKey'].dropna().unique())
    dtComp = create_summary_row(dtPmts, 'All Trusts')
    for s in sec_keys:
        row = create_summary_row(dtPmts.loc[dtPmts['securitizationKey'] == s], s)
        if row is not None:
            dtComp = pd.concat([dtComp, row], axis=0)
            log.info("Calculated strats for: %s", s)
    return dtComp


# ---------------------------------------------------------------------------
# Performance time series
# ---------------------------------------------------------------------------

def create_performance(dtPmts: pd.DataFrame, axisStr: str, assetStr: str) -> pd.DataFrame:
    """Per-securitization performance metrics indexed by ``axisStr``.

    Returns a wide DataFrame with columns ``{sec} ABSspeed``, ``{sec} Def``,
    ``{sec} Delq60``, ``{sec} CNL`` for each securitization + 'All Trusts'.
    """
    sec_keys = list(np.sort(dtPmts['securitizationKey'].dropna().unique())) + ['All Trusts']
    axis_vals = np.sort(dtPmts[axisStr].dropna().unique())

    def _empty(suffix: str) -> pd.DataFrame:
        return pd.DataFrame(
            data=np.full((len(axis_vals), len(sec_keys)), np.nan),
            index=axis_vals,
            columns=[f"{s} {suffix}" for s in sec_keys],
        )

    ABSTable = _empty('ABSspeed')
    ppTable = _empty('Prepays')
    delq30Table = _empty('Delq30')
    delq60Table = _empty('Delq60')
    delq90Table = _empty('Delq90')
    defTable = _empty('Def')
    CNLTable = _empty('CNL')

    for s in sec_keys:
        if 'Trust' not in s:
            continue
        sec_slice = dtPmts if s == 'All Trusts' else dtPmts.loc[dtPmts['securitizationKey'] == s]
        if sec_slice.empty:
            continue

        if assetStr == 'Auto Loans':
            grp_cols = ['reportingPeriodBeginningLoanBalanceAmount', 'chargedoffPrincipalAmount',
                        'reportingPeriodActualEndBalanceAmount', 'actualPrincipalCollectedAmount',
                        'otherPrincipalAdjustmentAmount', 'scheduledPrincipalAmount']
            grp_cols = [c for c in grp_cols if c in sec_slice.columns]
            dtSMM = sec_slice.groupby(axisStr)[grp_cols].sum()
            unscheduled = (dtSMM['actualPrincipalCollectedAmount']
                           + dtSMM['otherPrincipalAdjustmentAmount']
                           + dtSMM['chargedoffPrincipalAmount']
                           - dtSMM['scheduledPrincipalAmount'])
            denom = dtSMM['reportingPeriodBeginningLoanBalanceAmount'] - dtSMM['scheduledPrincipalAmount']
            smm = unscheduled / denom.replace(0, np.nan)
            wavg_age = WAVG(sec_slice, 'ageFromCutoffDate', 'beginningBalanceAtCutoffDate')
            ABSTable[f"{s} ABSspeed"] = smm / (1 - smm * (wavg_age - 1))

        elif assetStr == 'Auto Leases':
            grp_cols = ['reportingPeriodBeginningLoanBalanceAmount',
                        'reportingPeriodActualEndBalanceAmount', 'ageFromCutoffDate',
                        'beginningBalanceAtCutoffDate', 'acquisitionCost',
                        'scheduledSecuritizationBeginValueAmount',
                        'scheduledSecuritizationEndValueAmount']
            grp_cols = [c for c in grp_cols if c in sec_slice.columns]
            dtSMM = sec_slice.groupby(axisStr)[grp_cols].sum()
            surv = (
                1
                - (dtSMM['reportingPeriodBeginningLoanBalanceAmount']
                   / dtSMM['scheduledSecuritizationBeginValueAmount'].replace(0, np.nan))
                / (dtSMM['reportingPeriodActualEndBalanceAmount']
                   / dtSMM['scheduledSecuritizationEndValueAmount'].replace(0, np.nan))
            )
            wavg_age = WAVG(sec_slice, 'ageFromCutoffDate', 'beginningBalanceAtCutoffDate')
            ABSTable[f"{s} ABSspeed"] = surv / (1 + surv * wavg_age)

        if 'principalPrepaid' in sec_slice.columns:
            num = sec_slice.groupby(axisStr)['principalPrepaid'].sum()
            den = sec_slice.groupby(axisStr)['reportingPeriodBeginningLoanBalanceAmount'].sum()
            ppTable[f"{s} Prepays"] = num / den.replace(0, np.nan)

        delqTable = pd.pivot_table(
            data=sec_slice,
            values='reportingPeriodActualEndBalanceAmount',
            index=axisStr,
            columns='monthsDelinquent',
            aggfunc='sum',
        )
        denom = delqTable.sum(axis=1)
        if 1 in delqTable.columns:
            delq30Table[f"{s} Delq30"] = delqTable[1] / denom
        if 2 in delqTable.columns:
            delq60Table[f"{s} Delq60"] = delqTable[2] / denom
        if 3 in delqTable.columns:
            delq90Table[f"{s} Delq90"] = delqTable[3] / denom
        if 5 in delqTable.columns:
            defTable[f"{s} Def"] = delqTable[5] / denom

        if axisStr == 'monthsFromCutoffDate' and 'beginningBalanceAtCutoffDate' in sec_slice.columns:
            CNLstr = 'beginningBalanceAtCutoffDate'
            divisor = float(
                sec_slice.loc[sec_slice['monthsFromCutoffDate'] == 0, CNLstr].sum()
            )
        else:
            CNLstr = 'originalLoanAmount'
            divisor = float(
                sec_slice.loc[sec_slice['monthsFromCutoffDate'] == 0, CNLstr].sum()
            )
        if divisor and 'netLosses' in sec_slice.columns:
            CNLTable[f"{s} CNL"] = (
                sec_slice.groupby(axisStr)['netLosses'].sum() / divisor
            ).cumsum()

        log.info("Calculated performance for: %s on axis: %s", s, axisStr)

    return pd.concat([ABSTable, defTable, delq60Table, CNLTable], axis=1)


# ---------------------------------------------------------------------------
# Life-of-deal curves and roll rates
# ---------------------------------------------------------------------------

def create_curves(dtPmts: pd.DataFrame, axisStr: str) -> pd.DataFrame:
    """Lifetime balance curves by delinquency state, broken out by prime tier."""
    fieldStr = ['Curr', '1mDelq', '2mDelq', '3mDelq', '3m+Delq', 'Def', 'PP']
    primeStates = ['Other', 'Subprime <640', 'Nearprime 640-680', 'Prime 680-740', 'Superprime >740']

    axis_vals = np.sort(dtPmts[axisStr].dropna().unique())
    cols: list[str] = []
    pieces: dict[str, pd.DataFrame] = {}

    for p, prime_name in enumerate(primeStates):
        sec_slice = dtPmts.loc[dtPmts['primeIndicator'] == p]
        if sec_slice.empty:
            continue
        dtBal = pd.pivot_table(
            data=sec_slice,
            values='reportingPeriodBeginningLoanBalanceAmount',
            columns='monthsDelinquent',
            index=axisStr,
            aggfunc='sum',
        ).fillna(0).cumsum()
        orig_sum = float(sec_slice['originalLoanAmount'].sum())
        if orig_sum:
            dtBal = (dtBal / orig_sum).reindex(axis_vals).ffill()
        dtBal.columns = [f"{prime_name} {fieldStr[int(c)]}" if int(c) < len(fieldStr) else str(c)
                         for c in dtBal.columns]
        pieces[prime_name] = dtBal
        cols.extend([f"{prime_name} {f}" for f in fieldStr])
        log.info("Created curve for prime category: %s", prime_name)

    if not pieces:
        return pd.DataFrame()
    out = pd.concat(pieces.values(), axis=1).reindex(axis_vals).ffill()
    # Reorder by metric grouping (PP, Def, 1m, 2m, 3m, 3m+)
    order = [c for s in ('PP', 'Def', '1mDelq', '2mDelq', '3mDelq', '3m+Delq')
             for c in out.columns if s in c]
    return out[order] if order else out


def create_rollrates_matrix(dtPmts: pd.DataFrame) -> pd.DataFrame:
    """Single-period delinquency-state transition matrix."""
    if 'reportingPeriodActualEndBalanceAmount' not in dtPmts.columns:
        return pd.DataFrame(index=const.rrCols(), columns=const.rrCols())
    states = np.sort(dtPmts['monthsDelinquent'].dropna().unique())
    n_states = len(const.rrCols())
    out = np.zeros((n_states, n_states))

    # Pre-index by position so .iloc[i+1] is safe.
    dt = dtPmts.reset_index(drop=True)
    next_md = dt['monthsDelinquent'].shift(-1)
    bal = dt['reportingPeriodActualEndBalanceAmount']

    for st in states:
        for end_state in states:
            pre_mask = (dt['monthsDelinquent'] == st) & (dt['summaryDate'] == 0)
            post_mask = pre_mask & (next_md == end_state)
            den = float(bal.where(pre_mask).sum())
            if den:
                out[int(st), int(end_state)] = float(bal.where(post_mask).sum()) / den
    return pd.DataFrame(out, index=const.rrCols(), columns=const.rrCols())


def create_rollrates_ts(dtPmts: pd.DataFrame, axisStr: str) -> pd.DataFrame:
    """Time-series of roll rates by axis: start-state x end-state x period."""
    dt = dtPmts.reset_index(drop=True)
    if 'reportingPeriodActualEndBalanceAmount' not in dt.columns:
        return pd.DataFrame()
    axis_vals = np.sort(dt[axisStr].dropna().unique())
    if len(axis_vals) < 2:
        return pd.DataFrame()
    axis_vals = axis_vals[:-1]  # last period has no next

    next_md = dt['monthsDelinquent'].shift(-1)
    bal = dt['reportingPeriodActualEndBalanceAmount']

    n_start = len(const.rrCols()) - 2
    n_end = len(const.rrCols())
    cols = [f"{const.rrCols()[s]}-{const.rrCols()[e]}"
            for s in range(n_start) for e in range(n_end)]
    out = pd.DataFrame(np.zeros((len(axis_vals), len(cols))), index=axis_vals, columns=cols)

    for s in range(n_start):
        for e in range(n_end):
            col = f"{const.rrCols()[s]}-{const.rrCols()[e]}"
            for ind in axis_vals:
                pre_mask = (
                    (dt[axisStr] == ind)
                    & (dt['monthsDelinquent'] == s)
                    & (dt['summaryDate'] == 0)
                )
                post_mask = pre_mask & (next_md == e)
                den = float(bal.where(pre_mask).sum())
                if den:
                    out.at[ind, col] = float(bal.where(post_mask).sum()) / den
            log.debug("Roll-rate %s on axis %s done", col, axisStr)
    return out


# ---------------------------------------------------------------------------
# Plotting (optional — lazy import)
# ---------------------------------------------------------------------------

def plot_performance(dtPmts: pd.DataFrame) -> None:
    """Plot ABS speed / prepays / delq / CNL for each performance axis."""
    import matplotlib.pyplot as plt  # noqa: PLC0415
    plt.rcParams.update({'font.size': 8})
    fig, axArr = plt.subplots(3, 6)
    fig.suptitle('ALD Performance Charts')

    axisStr = ['monthsFromCutoffDate', 'age', 'reportingPeriodBeginningDate']
    cols = ['ABSspeed', 'Prepays', 'Delq30', 'Delq60', 'Delq90', 'CNL']
    for i, axis in enumerate(axisStr):
        dtPerf = create_performance(dtPmts, axis, 'Auto Loans')
        for j, c in enumerate(cols):
            mask = [c in col for col in dtPerf.columns]
            ax = axArr[i, j]
            ax.set_xlabel(axis)
            legend = (j == 0) and (i == 0)
            dtPerf.loc[:, mask].plot(kind='line', ax=ax, legend=legend)
            if legend:
                ax.legend(bbox_to_anchor=(.75, -2.62), loc=2, borderaxespad=0., ncol=4,
                          prop={'size': 6})
            if i == 0:
                ax.set_title(c)


def plot_model_outputs(auc, oob, rocY, rocX, nTrees, headers, featImp, superTitle):
    """ROC / variable importance / AUC / OOB error panel for a random-forest run."""
    import matplotlib.pyplot as plt  # noqa: PLC0415
    plt.rcParams.update({'font.size': 8})
    fig, axArr = plt.subplots(2, 2)
    fig.suptitle(superTitle)

    ctClass = [i * 0.01 for i in range(0, 101)]
    axArr[0, 0].plot(ctClass, ctClass, label='x=y', linestyle=':')
    for i in range(rocY.shape[0]):
        axArr[0, 0].plot(rocX, rocY[i, :], label=f"ROC Curve for iTrees:{nTrees[i]}", linewidth=2)
    axArr[0, 0].set_xlabel('False Positive Rate')
    axArr[0, 0].set_ylabel('True Positive Rate')
    axArr[0, 0].set_title('ROCs vs No. of Trees')

    tol = const.featTolerance()
    axArr[0, 1].barh(np.arange(tol) + .5, featImp[:tol], align='center')
    axArr[0, 1].set_yticks(np.arange(tol) + .5)
    axArr[0, 1].set_yticklabels(headers[:tol])
    axArr[0, 1].set_title('Var Importance for No. of Trees Range Mid')

    axArr[1, 0].plot(list(nTrees), auc)
    axArr[1, 0].set_xlabel('No. of Trees')
    axArr[1, 0].set_ylabel('AUC')
    axArr[1, 0].set_title('AUC vs No. of Trees')

    axArr[1, 1].plot(list(nTrees), oob)
    axArr[1, 1].set_xlabel('No. of Trees')
    axArr[1, 1].set_ylabel('OOB Error')
    axArr[1, 1].set_title('OOB Error (1-OOB Score) vs No. of Trees')


# ---------------------------------------------------------------------------
# Random-forest heatmap (optional — lazy import sklearn)
# ---------------------------------------------------------------------------

def generate_regressors(dtR: pd.DataFrame) -> pd.DataFrame:
    """One-hot / decile-bucket a numeric/string DataFrame for tree models.

    Carries forward the original case-splits (frequent value, multi-value,
    decile-bin, fall-through) and adds a per-column 'is null' indicator when
    any values are missing.
    """
    topTolerance, bottomTolerance = .9, .1
    freqTolerance, sparseTolerance = .15, .2
    binTolerance = 10
    bins = list(range(0, int((1 - sparseTolerance) * 100), binTolerance))

    sparseCount = dtR.count(axis=0) / dtR.shape[0]
    cols: list[str] = []
    dtOut = np.zeros((dtR.shape[0], 0))

    for i in range(dtR.shape[1]):
        col_name = dtR.columns.values[i]
        col_series = dtR.iloc[:, i]
        freqTable = col_series.value_counts(normalize=True)

        if freqTable.shape[0] == 0:
            log.debug("Case 0 (all NaN): %s", col_name)
        elif (freqTable.iloc[0] >= topTolerance
              and sparseCount[i] > sparseTolerance
              and freqTable.shape[0] > 1):
            cols.append(f"{col_name}_is: {freqTable.index[0]}")
            block = np.zeros((dtR.shape[0], 1))
            block[(col_series == freqTable.index[0]).to_numpy(), 0] = 1
            dtOut = np.concatenate((dtOut, block), axis=1)
            log.debug("Case 1: %s", col_name)
        elif (bottomTolerance <= freqTable.iloc[0] < topTolerance
              and sparseCount[i] > sparseTolerance
              and freqTable.shape[0] > 1):
            uniqCols = freqTable.index[(freqTable > freqTolerance).to_numpy().nonzero()[0]]
            if uniqCols.shape[0] == freqTable.shape[0]:
                uniqCols = uniqCols[:-1]
            block = np.zeros((dtR.shape[0], uniqCols.shape[0]))
            for j, u in enumerate(uniqCols):
                cols.append(f"{col_name}_is: {u}")
                block[(col_series == u).to_numpy(), j] = 1
            dtOut = np.concatenate((dtOut, block), axis=1)
            log.debug("Case 2: %s", col_name)
        elif sparseCount[i] > sparseTolerance and freqTable.shape[0] > 1:
            block = np.zeros((dtR.shape[0], len(bins)))
            for j, u in enumerate(bins):
                lo = np.nanpercentile(col_series, u)
                hi = np.nanpercentile(col_series, u + binTolerance)
                in_bin = ((col_series >= lo) & (col_series < hi)).to_numpy()
                block[in_bin, j] = 1
                cols.append(f"{col_name}_bin: {u}")
            dtOut = np.concatenate((dtOut, block), axis=1)
            log.debug("Case 3: %s", col_name)
        else:
            log.debug("Case 4 (too sparse / one-value): %s", col_name)

        n_null = int(col_series.isna().sum())
        if 0 < n_null < dtR.shape[0]:
            cols.append(f"{col_name}_is: null")
            block = np.zeros((dtR.shape[0], 1))
            block[col_series.isna().to_numpy(), 0] = 1
            dtOut = np.concatenate((dtOut, block), axis=1)

    log.info("Finished generating regressors (%d columns)", len(cols))
    return pd.DataFrame(data=dtOut, index=dtR.index, columns=cols)


def create_heatmap(dtPmts: pd.DataFrame, trustStr: str) -> pd.DataFrame:
    """Train a random forest on the prime/delq target and return ranked feature importance."""
    from sklearn import ensemble, metrics  # noqa: PLC0415

    if trustStr == 'All':
        dtX = dtPmts[const.modelFields()].sample(frac=const.frac(), replace=True)
    else:
        dtX = dtPmts.loc[dtPmts['securitizationKey'] == trustStr, const.modelFields()]
    Y = (
        (dtPmts.loc[dtX.index, 'monthsDelinquent'] > 1)
        & (dtPmts.loc[dtX.index, 'monthsDelinquent'] < 6)
    ).to_numpy().ravel()
    dtX = generate_regressors(dtX)

    nTrees = range(100, 110, 10)
    auc: list[float] = []
    oob: list[float] = []
    rocX = np.arange(0, 1.01, .01)
    rocY = np.zeros((len(nTrees), rocX.shape[0]))
    rfModel = None

    for i, trees in enumerate(nTrees):
        log.info("RF trial %d trees=%d", i, trees)
        rfModel = ensemble.RandomForestClassifier(
            n_estimators=trees, max_depth=const.maxDepth(),
            max_features='sqrt', bootstrap=True, oob_score=True,
            random_state=531, min_samples_leaf=const.maxLeaf(),
        )
        rfModel.fit(dtX, Y)
        predVector = rfModel.predict_proba(dtX)
        if predVector.shape[1] > 1:
            fpr, tpr, _ = metrics.roc_curve(Y, predVector[:, 1])
            rocY[i, :] = np.interp(rocX, fpr, tpr)
            auc.append(metrics.roc_auc_score(Y, predVector[:, 1]))
            oob.append(1 - rfModel.oob_score_)
        else:
            log.warning("No model differences in Y for trial %d", i)

    if rfModel is None:
        return pd.DataFrame(columns=['colName', 'colImp'])

    featImp = rfModel.feature_importances_ / rfModel.feature_importances_.max()
    order = np.argsort(featImp)[::-1]
    return pd.DataFrame({
        'colName': dtX.columns.values[order],
        'colImp': featImp[order],
    })


def get_heatmap_cols(dtCol: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Bucket a column for heatmap display. Returns (bucket-index, label) arrays."""
    dtHist = np.zeros((dtCol.shape[0], 1))
    if np.issubdtype(dtCol.dtype, np.number) and dtCol.nunique(dropna=True) > const.intNo():
        lo = float(dtCol.min())
        hi = float(dtCol.max())
        step = (hi - lo) / const.intNo()
        starts = lo + np.arange(0, const.intNo()) * step
        ends = lo + np.arange(1, const.intNo() + 1) * step
        labels = [f"{s:.1f}-{e:.1f}" for s, e in zip(starts, ends)]
        for c, (s, e) in enumerate(zip(starts, ends)):
            in_bin = ((dtCol >= s) & (dtCol < e) & dtCol.notna()).to_numpy()
            dtHist[in_bin, 0] = c
    else:
        labels = list(np.sort(dtCol.dropna().unique()))
        for c, lab in enumerate(labels):
            dtHist[(dtCol == lab).to_numpy(), 0] = c
    return dtHist, np.array(labels)


def plot_heatmaps(dtPmts: pd.DataFrame, dtImp: pd.DataFrame, trustStr: str) -> pd.DataFrame:
    """Plot per-trust feature-interaction heatmaps. Saves to Heatmaps/{trust}.png."""
    import matplotlib.pyplot as plt  # noqa: PLC0415
    import seaborn as sns  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    dtTrust = dtPmts.loc[dtPmts['securitizationKey'] == trustStr]
    plt.rcParams.update({'font.size': 6})
    fig, axArr = plt.subplots(2, 3)
    fig.suptitle(f'Important Features Heatmaps for {trustStr} (Z = Mean of Def + Delq > 1m)')

    dtImp = dtImp.copy()
    dtImp['fieldname'] = [f.split('_', 1)[0] for f in dtImp['colName'].values]
    drop = np.isin(dtImp['fieldname'], const.badFields())
    dtImp = dtImp.loc[~drop]
    fieldsList = dtImp['fieldname'].drop_duplicates(keep='first').iloc[:4].values
    impactFields = fieldsList.copy()

    pairs = list(it.combinations(fieldsList, 2))
    delq_mask = (dtTrust['monthsDelinquent'] > 1) & (dtTrust['monthsDelinquent'] < 6)
    for idx, (fa, fb) in enumerate(pairs):
        log.info("Heatmap for %s x %s", fa, fb)
        i, j = idx // 3, idx % 3
        xCol, xCat = get_heatmap_cols(dtTrust[fa])
        yCol, yCat = get_heatmap_cols(dtTrust[fb])
        bad = delq_mask.astype(float).to_numpy().reshape(-1)
        dtHeatmap = pd.DataFrame({fa: xCol.ravel(), fb: yCol.ravel(), 'BadHombres': bad})
        pivot = pd.pivot_table(data=dtHeatmap, index=fa, columns=fb, values='BadHombres')
        pivot.index = xCat[pivot.index.astype(int)]
        pivot.columns = yCat[pivot.columns.astype(int)]
        sns.set(font_scale=.75)
        sns.heatmap(data=pivot, xticklabels=True, yticklabels=True,
                    ax=axArr[i, j], annot=True, annot_kws={"size": 6}, cbar=False)
        axArr[i, j].set_xlabel(fb, fontsize=7)
        axArr[i, j].set_ylabel(fa, fontsize=7)
        axArr[i, j].set_xticklabels(axArr[i, j].get_xticklabels(), rotation=25, fontsize=6)
        axArr[i, j].set_yticklabels(axArr[i, j].get_yticklabels(), rotation=45, fontsize=6)

    out_dir = Path(__file__).resolve().parent / 'Heatmaps'
    out_dir.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_dir / f"{trustStr}.png", dpi=200)
    plt.close()
    return pd.DataFrame(data=impactFields, columns=[trustStr],
                        index=np.arange(0, impactFields.shape[0]))


def run_heatmaps(dtPmts: pd.DataFrame) -> pd.DataFrame:
    """Run create_heatmap + plot_heatmaps for every securitization."""
    sec_keys = np.sort(dtPmts['securitizationKey'].dropna().unique())
    dtFields = pd.DataFrame()
    for s in sec_keys:
        log.info("Heatmap for %s", s)
        dtImp = create_heatmap(dtPmts, s)
        if not dtImp['colImp'].isna().all():
            dtFields = pd.concat([dtFields, plot_heatmaps(dtPmts, dtImp, s)], axis=1)
    return dtFields


def describe_data(dtPmts: pd.DataFrame) -> pd.DataFrame:
    """Per-securitization ``.describe()`` over non-numeric fields, saved as CSV."""
    df = dtPmts.drop(columns=[c for c in ('subvented', 'modificationTypeCode')
                              if c in dtPmts.columns])
    sec_keys = np.sort(df['securitizationKey'].dropna().unique())
    parts = []
    for s in sec_keys:
        log.info("Describe %s", s)
        sec = df.loc[df['securitizationKey'] == s]
        try:
            piece = sec.describe(percentiles=[.25, .5, .75], exclude=[np.number])
        except (ValueError, TypeError):
            piece = pd.DataFrame()
        piece.index = [f"{s} {idx}" for idx in piece.index]
        parts.append(piece)
    out = pd.concat(parts, axis=0) if parts else pd.DataFrame()
    return out
