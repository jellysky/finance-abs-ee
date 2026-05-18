"""Clean and enrich raw auto-lease ABS-EE data into a normalized payments DataFrame.

Public surface (unchanged from the 2017 version):
    const.<field-list>()          # schema-level column lists
    clean_ald_files(df)           # normalize dates/numerics/codes/dedup
    append_calc_fields(df)        # derive securitization balances, age, LTV, etc.
    data_vetting(df)              # (errors, descNum, descStr) per securitization
    cashflow_vetting(df)          # per-month cashflow rollup
    fit_reporting_model(df)       # rename lease-side columns to loan-side equivalents
    describe_raw_data(df)         # generic field description per securitization

Differences vs. the original:
- Imports cleanly (no broken ``def main(...)`` scratchpad at the bottom).
- All ``df['col'].iloc[idx] = v`` chained assignments are now ``df.loc[mask, 'col'] = v``,
  which is safe under pandas 2.x copy-on-write.
- ``np.pv`` and ``np.rate`` (removed from NumPy 1.20+) now route through
  ``numpy_financial``.
- The duplicate-charge-off / liquidation check (originally a positional
  comparison via ``Series.iloc[:-1] == Series.iloc[1:]``) is now done on raw
  numpy arrays — the original silently produced an all-False mask because
  pandas aligned the two halves on their (non-overlapping) indices.
- Column lookups are defensive: absent optional fields no longer KeyError.
- Rate scaling only divides values >1 by 100 (was: whole column).
- Unmapped manufacturer / model names fall back to the raw name (was: 'N/A').
- ``data_vetting`` sorts before slicing, uses ``.duplicated()`` instead of the
  removed ``MultiIndex.get_duplicates``, and replaces the O(N²) charge-off
  check with a groupby/merge.
- ``clean_ald_files`` copies its input rather than mutating it.
- Lookup CSVs are resolved relative to this file, not the process cwd.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import numpy as np
import numpy_financial as npf
import pandas as pd

ROOT = Path(__file__).resolve().parent
INPUTS = ROOT / "Inputs"
log = logging.getLogger("absee.autolease")


class const:
    @staticmethod
    def booleanFields():
        return ['assetAddedIndicator', 'assetSubjectDemandIndicator',
                'coLesseePresentIndicator', 'reportingPeriodModificationIndicator',
                'underwritingIndicator']

    @staticmethod
    def dateFields():
        return ['originalFirstPaymentDate', 'originationDate', 'paidThroughDate',
                'reportingPeriodBeginDate', 'reportingPeriodEndDate',
                'scheduledTerminationDate', 'zeroBalanceEffectiveDate']

    @staticmethod
    def debugFieldsRaw():
        return ['assetNumber', 'securitizationKey', 'reportingPeriodBeginDate',
                'reportingPeriodEndDate', 'reportingPeriodEndingActualBalanceAmount',
                'totalActualAmountPaid', 'reportingPeriodScheduledPaymentAmount',
                'contractResidualValue', 'acquisitionCost', 'vehicleValueAmount',
                'reportingPeriodSecuritizationValueAmount',
                'reportingPeriodEndActualSecuritizationAmount', 'chargedOffAmount',
                'liquidationProceedsAmount', 'repurchaseAmount', 'baseResidualValue',
                'securitizationDiscountRate', 'currentDelinquencyStatus',
                'remainingTermNumber', 'originalLeaseTermNumber']

    @staticmethod
    def debugFieldsClean():
        return ['assetNumber', 'securitizationKey', 'reportingPeriodBeginDate',
                'reportingPeriodEndingActualBalanceAmount', 'totalActualAmountPaid',
                'actualOtherCollectedAmount', 'reportingPeriodScheduledPaymentAmount',
                'contractResidualValue', 'acquisitionCost', 'vehicleValueAmount',
                'reportingPeriodSecuritizationValueAmount',
                'reportingPeriodEndActualSecuritizationAmount',
                'actualPrincipalCollectedAmount', 'actualInterestCollectedAmount',
                'otherPrincipalAdjustmentAmount', 'principalPrepaid', 'chargedOffAmount',
                'recoveredAmount', 'liquidationProceedsAmount',
                'scheduledSecuritizationBeginValueAmount',
                'scheduledSecuritizationEndValueAmount',
                'scheduledSecuritizationValueAmortization',
                'scheduledSecuritizationValueInterest', 'securitizationDiscountRate',
                'baseResidualValue', 'originalInterestRatePercentage',
                'currentDelinquencyStatus', 'monthsDelinquent', 'remainingTermNumber',
                'originalLeaseTermNumber', 'terminationIndicator',
                'nextReportingPeriodPaymentAmountDue', 'lesseeGeographicLocation',
                'saleGainOrLoss', 'zeroBalanceCode']

    @staticmethod
    def decimalFields():
        return ['acquisitionCost', 'actualOtherCollectedAmount', 'baseResidualValue',
                'chargedOffAmount', 'contractResidualValue', 'excessFeeAmount',
                'liquidationProceedsAmount', 'nextReportingPeriodPaymentAmountDue',
                'otherAssessedUncollectedServicerFeeAmount',
                'otherLeaseLevelServicingFeesRetainedAmount',
                'reportingPeriodEndActualSecuritizationAmount',
                'reportingPeriodEndingActualBalanceAmount',
                'reportingPeriodScheduledPaymentAmount',
                'reportingPeriodSecuritizationValueAmount', 'repurchaseAmount',
                'servicerAdvancedAmount', 'servicingFlatFeeAmount',
                'totalActualAmountPaid', 'vehicleValueAmount']

    @staticmethod
    def integerFields():
        return ['baseResidualSourceCode', 'currentDelinquencyStatus', 'gracePeriod',
                'leaseExtended', 'lesseeCreditScore',
                'lesseeEmploymentVerificationCode',
                'lesseeIncomeVerificationLevelCode', 'modificationTypeCode',
                'originalLeaseTermNumber', 'paymentTypeCode', 'remainingTermNumber',
                'servicingAdvanceMethodCode', 'terminationIndicator',
                'vehicleModelYear', 'vehicleNewUsedCode', 'vehicleTypeCode',
                'vehicleValueSourceCode', 'zeroBalanceCode']

    @staticmethod
    def listFields():
        return ['subvented']

    @staticmethod
    def rateFields():
        return ['paymentToIncomePercentage', 'securitizationDiscountRate',
                'servicingFeePercentage']

    @staticmethod
    def stringFields():
        return ['assetNumber', 'assetTypeNumber', 'lesseeCreditScoreType',
                'lesseeGeographicLocation', 'originatorName',
                'primaryLeaseServicerName', 'securitizationKey', 'shelf',
                'vehicleManufacturerName', 'vehicleModelName']

    @staticmethod
    def rawCols():
        return ['Count', 'OpenBal', 'StartMonth', 'EndMonth', 'MissingMonths', 'Walk',
                'IncrBal', 'Pmts', 'Missing', 'Extra', 'COExtra', 'Dupes', 'NegOpenBal',
                'NegCloseBal', 'RateNeg', 'RatePos', 'Integer', 'NegCO', 'PartialCO',
                'GreaterCO', 'NegRepo', 'NegRecov']

    @staticmethod
    def minSens(): return .01
    @staticmethod
    def divInd(): return .4
    @staticmethod
    def divMax(): return 5
    @staticmethod
    def primeTiers():
        return [(740, np.inf, 4), (680, 740, 3), (640, 680, 2), (-np.inf, 640, 1)]
    @staticmethod
    def validCreditScoreRange(): return (300, 850)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_dict(name: str) -> pd.DataFrame:
    """Read a lookup CSV from Inputs/ relative to this module."""
    return pd.read_csv(INPUTS / name)


def _present(cols: Iterable[str], df: pd.DataFrame) -> list[str]:
    return [c for c in cols if c in df.columns]


def _merge_lookup(df: pd.DataFrame, raw_col: str, dict_csv: str) -> pd.DataFrame:
    """Map ``df[raw_col]`` via ``dict_csv`` (old->new) with raw-fallback.

    Originally the cleaner dropped the original column after the merge, then
    set unmapped names to 'N/A'. That silently nuked any name not in the
    dictionary. We now fall back to the raw value before applying 'N/A' for
    truly missing rows.
    """
    if raw_col not in df.columns:
        return df
    try:
        d = read_dict(dict_csv)
    except FileNotFoundError:
        log.warning("Inputs/%s missing; leaving %s raw.", dict_csv, raw_col)
        return df
    out = df.merge(d, how='left', left_on=raw_col, right_on='old')
    out[raw_col] = out['new'].fillna(out[raw_col])
    out = out.drop(columns=[c for c in ('old', 'new') if c in out.columns])
    out[raw_col] = out[raw_col].fillna('N/A')
    return out


# ---------------------------------------------------------------------------
# Step 1: clean
# ---------------------------------------------------------------------------

def clean_ald_files(dtPmts: pd.DataFrame) -> pd.DataFrame:
    """Normalize raw ABS-EE auto-lease data."""
    dtPmts = dtPmts.copy()

    # Drop Daimler if present (legacy filter from original)
    if 'securitizationKey' in dtPmts.columns:
        dtPmts = dtPmts.loc[dtPmts['securitizationKey'] != 'DAIMLER TRUST LEASING LLC']

    # --- Dates ---
    date_formats = {
        'reportingPeriodBeginDate': '%m-%d-%Y',
        'reportingPeriodEndDate': '%m-%d-%Y',
        'paidThroughDate': '%m-%d-%Y',
        'originationDate': '%m/%Y',
        'originalFirstPaymentDate': '%m/%Y',
        'zeroBalanceEffectiveDate': '%m/%Y',
        'scheduledTerminationDate': '%m/%Y',
    }
    for col, fmt in date_formats.items():
        if col in dtPmts.columns:
            dtPmts[col] = pd.to_datetime(dtPmts[col], format=fmt, errors='coerce')
    log.info("Cleaned dates")

    # --- Coerce numerics ---
    numeric_cols = _present(
        const.decimalFields() + const.integerFields() + const.rateFields(), dtPmts
    )
    for c in numeric_cols:
        dtPmts[c] = pd.to_numeric(dtPmts[c], errors='coerce')
    log.info("Coerced %d numeric fields", len(numeric_cols))

    # --- Default-zero event-amount columns (added if absent — happens for
    # brand-new pools where no charge-off / liquidation / repurchase /
    # termination has occurred yet).
    zero_default = [
        'chargedOffAmount', 'liquidationProceedsAmount', 'repurchaseAmount',
        'recoveredAmount', 'terminationIndicator', 'zeroBalanceCode',
    ]
    for c in zero_default:
        if c in dtPmts.columns:
            dtPmts[c] = dtPmts[c].fillna(0)
        else:
            dtPmts[c] = 0.0

    # --- Fill-if-present (column absence is a real signal for these).
    fill_if_present = [
        'contractResidualValue', 'excessFeeAmount',
        'otherAssessedUncollectedServicerFeeAmount',
        'otherLeaseLevelServicingFeesRetainedAmount', 'totalActualAmountPaid',
        'lesseeIncomeVerificationLevelCode', 'lesseeEmploymentVerificationCode',
        'lesseeCreditScore', 'actualOtherCollectedAmount',
        'currentDelinquencyStatus', 'paymentToIncomePercentage',
    ]
    for c in _present(fill_if_present, dtPmts):
        dtPmts[c] = dtPmts[c].fillna(0)

    # --- Fix "double month" rows (period covers 2 months) ---
    # Shift begin date forward by a month, then pull prior month's end-sec-value
    # in to replace the current row's begin-sec-value.
    if all(c in dtPmts.columns for c in ('reportingPeriodBeginDate', 'reportingPeriodEndDate')):
        beg = dtPmts['reportingPeriodBeginDate']
        end = dtPmts['reportingPeriodEndDate']
        gap = (12 * end.dt.year + end.dt.month) - (12 * beg.dt.year + beg.dt.month)
        dbl_mask = (gap == 1)
        if dbl_mask.any():
            dtPmts.loc[dbl_mask, 'reportingPeriodBeginDate'] = (
                dtPmts.loc[dbl_mask, 'reportingPeriodBeginDate'] + pd.DateOffset(months=1)
            )
            if all(c in dtPmts.columns for c in
                   ('assetNumber', 'securitizationKey',
                    'reportingPeriodEndActualSecuritizationAmount',
                    'reportingPeriodSecuritizationValueAmount')):
                dtLast = dtPmts[['assetNumber', 'securitizationKey',
                                  'reportingPeriodBeginDate',
                                  'reportingPeriodEndActualSecuritizationAmount']].copy()
                dtLast['reportingPeriodBeginDate'] = (
                    dtLast['reportingPeriodBeginDate'] + pd.DateOffset(months=1)
                )
                dtPmts = dtPmts.merge(
                    dtLast, how='left',
                    on=['assetNumber', 'securitizationKey', 'reportingPeriodBeginDate'],
                    suffixes=('', '_y'),
                )
                if 'reportingPeriodEndActualSecuritizationAmount_y' in dtPmts.columns:
                    dtPmts.loc[dbl_mask, 'reportingPeriodSecuritizationValueAmount'] = (
                        dtPmts.loc[dbl_mask, 'reportingPeriodEndActualSecuritizationAmount_y']
                    )
                    dtPmts = dtPmts.drop(
                        columns=['reportingPeriodEndActualSecuritizationAmount_y']
                    )
            log.info("Fixed %d double-month rows", int(dbl_mask.sum()))

    # --- Dedup ---
    dedup_keys = _present(
        ['assetNumber', 'reportingPeriodBeginDate', 'securitizationKey'], dtPmts
    )
    if dedup_keys:
        before = len(dtPmts)
        dtPmts = (
            dtPmts.drop_duplicates(subset=dedup_keys, keep='last')
                  .sort_values(by=dedup_keys)
                  .reset_index(drop=True)
        )
        if len(dtPmts) != before:
            log.info("Dropped %d duplicate rows on %s", before - len(dtPmts), dedup_keys)

    # --- Manufacturer / model name normalization with raw fallback ---
    dtPmts = _merge_lookup(dtPmts, 'vehicleManufacturerName', 'manus.csv')
    dtPmts = _merge_lookup(dtPmts, 'vehicleModelName', 'model.csv')

    # --- Rate scaling per securitization (only values >1 are scaled) ---
    rate_cols = _present(const.rateFields(), dtPmts)
    secs = (
        dtPmts['securitizationKey'].dropna().unique()
        if 'securitizationKey' in dtPmts.columns else [None]
    )
    for r in rate_cols:
        for s in secs:
            sec_mask = (dtPmts['securitizationKey'] == s) if s is not None else slice(None)
            sub_valid = dtPmts.loc[sec_mask, r].dropna()
            if sub_valid.empty:
                continue
            frac_over_one = float((sub_valid > 1).mean())
            top = float(sub_valid.max())
            if frac_over_one > const.divInd() and top > const.divMax():
                target = sec_mask & (dtPmts[r] > 1) if s is not None else (dtPmts[r] > 1)
                dtPmts.loc[target, r] = dtPmts.loc[target, r] / 100
            elif top > const.divMax():
                target = (
                    sec_mask & (dtPmts[r] > const.divMax())
                    if s is not None else (dtPmts[r] > const.divMax())
                )
                dtPmts.loc[target, r] = np.nan
        dtPmts.loc[dtPmts[r] > 1, r] = np.nan
    if rate_cols:
        log.info("Normalized rate scaling on %d field(s)", len(rate_cols))

    # --- Servicing fee small-value floor ---
    if 'servicingFeePercentage' in dtPmts.columns:
        small = dtPmts['servicingFeePercentage'].abs() < const.minSens()
        dtPmts.loc[small, 'servicingFeePercentage'] = .01

    return dtPmts


# ---------------------------------------------------------------------------
# Step 2: enrich
# ---------------------------------------------------------------------------

def append_calc_fields(dtPmts: pd.DataFrame) -> pd.DataFrame:
    """Derive securitization balances, age, LTV, monthsDelinquent, prime tier, etc."""
    dtPmts = dtPmts.copy()
    sens = const.minSens()

    # --- Scheduled securitization beginning & ending values (PV at discount rate) ---
    pv_inputs = ('securitizationDiscountRate', 'remainingTermNumber',
                 'reportingPeriodScheduledPaymentAmount', 'baseResidualValue')
    if all(c in dtPmts.columns for c in pv_inputs):
        rate = dtPmts['securitizationDiscountRate'] / 12
        nper = dtPmts['remainingTermNumber']
        pmt = dtPmts['reportingPeriodScheduledPaymentAmount']
        fv = dtPmts['baseResidualValue']
        dtPmts['scheduledSecuritizationBeginValueAmount'] = npf.pv(rate, nper + 1, -pmt, -fv, 1)
        dtPmts['scheduledSecuritizationEndValueAmount'] = npf.pv(rate, nper, -pmt, -fv, 1)
        log.info("Computed scheduledSecuritization{Begin,End}ValueAmount")

    # --- Charge-off / liquidation reconciliation against beg-sec-value ---
    co_inputs = ('reportingPeriodSecuritizationValueAmount',
                 'reportingPeriodEndActualSecuritizationAmount',
                 'chargedOffAmount', 'remainingTermNumber',
                 'liquidationProceedsAmount')
    if all(c in dtPmts.columns for c in co_inputs):
        beg_sec = dtPmts['reportingPeriodSecuritizationValueAmount']
        end_sec = dtPmts['reportingPeriodEndActualSecuritizationAmount']
        co = dtPmts['chargedOffAmount']
        liq = dtPmts['liquidationProceedsAmount']
        rt = dtPmts['remainingTermNumber']

        # C1: beg_sec == 0 & end_sec != 0 & co < 0 & rt > 0 → co = beg_sec - end_sec
        c1 = (
            (beg_sec.abs() < sens) & (end_sec.abs() > sens) & (co < 0) & (rt > 0)
        )
        dtPmts.loc[c1, 'chargedOffAmount'] = (
            dtPmts.loc[c1, 'reportingPeriodSecuritizationValueAmount']
            - dtPmts.loc[c1, 'reportingPeriodEndActualSecuritizationAmount']
        )

        # C2: beg_sec == 0 & end_sec != 0 & co > 0 & rt > 0 → beg_sec = end_sec + co
        c2 = (
            (beg_sec.abs() < sens) & (end_sec.abs() > sens) & (co > 0) & (rt > 0)
        )
        dtPmts.loc[c2, 'reportingPeriodSecuritizationValueAmount'] = (
            dtPmts.loc[c2, ['reportingPeriodEndActualSecuritizationAmount',
                            'chargedOffAmount']].sum(axis=1)
        )

        # C3: co > 0 & co + liq == beg_sec → co := beg_sec
        c3 = (
            (co > 0)
            & ((beg_sec - co - liq).abs() < sens)
        )
        dtPmts.loc[c3, 'chargedOffAmount'] = dtPmts.loc[
            c3, 'reportingPeriodSecuritizationValueAmount'
        ]

    # --- Recovered & totalActualAmountPaid adjustments ---
    if all(c in dtPmts.columns for c in
           ('chargedOffAmount', 'liquidationProceedsAmount')):
        dtPmts['recoveredAmount'] = 0.0
        r1 = (dtPmts['chargedOffAmount'] > 0) & (dtPmts['liquidationProceedsAmount'] > 0)
        dtPmts.loc[r1, 'recoveredAmount'] = dtPmts.loc[r1, 'liquidationProceedsAmount']

        zero_bal_balances = ['reportingPeriodEndingActualBalanceAmount',
                             'reportingPeriodEndActualSecuritizationAmount']
        if all(c in dtPmts.columns for c in
               ('zeroBalanceEffectiveDate', 'reportingPeriodBeginDate',
                'zeroBalanceCode', *zero_bal_balances)):
            sum_end = dtPmts[zero_bal_balances].sum(axis=1)
            r2 = (
                (dtPmts['liquidationProceedsAmount'].abs() > sens)
                & (dtPmts['zeroBalanceEffectiveDate'] < dtPmts['reportingPeriodBeginDate'])
                & (sum_end.abs() < sens)
                & (dtPmts['zeroBalanceCode'] == 3)
            )
            dtPmts.loc[r2, 'recoveredAmount'] = (
                dtPmts.loc[r2, 'recoveredAmount']
                + dtPmts.loc[r2, 'liquidationProceedsAmount']
            )

            if 'terminationIndicator' in dtPmts.columns and 'totalActualAmountPaid' in dtPmts.columns:
                term12 = dtPmts['terminationIndicator'].isin([1, 2])
                liq_zero = (
                    (dtPmts['liquidationProceedsAmount'].abs() > sens)
                    & (dtPmts['zeroBalanceEffectiveDate'] < dtPmts['reportingPeriodBeginDate'])
                    & (sum_end.abs() < sens)
                    & (dtPmts['zeroBalanceCode'] == 1)
                )
                pay_mask = term12 | liq_zero
                dtPmts.loc[pay_mask, 'totalActualAmountPaid'] = (
                    dtPmts.loc[pay_mask, 'totalActualAmountPaid']
                    + dtPmts.loc[pay_mask, 'liquidationProceedsAmount']
                )

        # Zero out duplicated charge-off / recovery / liquidation rows. The
        # original used Series.iloc[:-1]==Series.iloc[1:], which under pandas
        # index alignment compares mismatched indices and silently produces
        # all-False. Use raw numpy arrays so the positional check is honored.
        if 'assetNumber' in dtPmts.columns:
            assets = dtPmts['assetNumber'].to_numpy()
            liqs = dtPmts['liquidationProceedsAmount'].to_numpy()
            n = len(dtPmts)
            dup_pos = np.zeros(n, dtype=bool)
            if n > 1:
                dup_pos[:-1] = (
                    (assets[:-1] == assets[1:])
                    & (liqs[:-1] == liqs[1:])
                    & (np.abs(liqs[:-1]) > sens)
                )
            dup_mask = pd.Series(dup_pos, index=dtPmts.index)
            for col in ('recoveredAmount', 'chargedOffAmount',
                        'liquidationProceedsAmount', 'zeroBalanceCode',
                        'terminationIndicator'):
                if col in dtPmts.columns:
                    dtPmts.loc[dup_mask, col] = 0

    # --- monthsDelinquent (0..4 normal, 5 = charged off, 6 = prepaid this period) ---
    if 'currentDelinquencyStatus' in dtPmts.columns:
        delq = dtPmts['currentDelinquencyStatus'].fillna(0).astype(float)
        md = np.floor(delq / 30).clip(upper=4)
        if 'chargedOffAmount' in dtPmts.columns:
            md = md.where(~(dtPmts['chargedOffAmount'] > 0), 5)
        prepay_inputs = ('reportingPeriodEndingActualBalanceAmount',
                         'remainingTermNumber', 'originalLeaseTermNumber',
                         'totalActualAmountPaid', 'reportingPeriodScheduledPaymentAmount')
        if all(c in dtPmts.columns for c in prepay_inputs):
            prepay = (
                (dtPmts['reportingPeriodEndingActualBalanceAmount'].abs() < sens)
                & (dtPmts['remainingTermNumber'] < dtPmts['originalLeaseTermNumber'])
                & (dtPmts['totalActualAmountPaid'] > dtPmts['reportingPeriodScheduledPaymentAmount'])
                & (delq == 0)
            )
            md = md.where(~prepay, 6)
        dtPmts['monthsDelinquent'] = md

    # --- Scheduled sec value amortization (principal-due-style) ---
    amort_inputs = ('scheduledSecuritizationBeginValueAmount',
                    'scheduledSecuritizationEndValueAmount',
                    'currentDelinquencyStatus', 'monthsDelinquent')
    if all(c in dtPmts.columns for c in amort_inputs):
        amort = pd.Series(0.0, index=dtPmts.index)
        in_pay = dtPmts['monthsDelinquent'] < 5
        amort = amort.where(~in_pay, (
            (dtPmts['scheduledSecuritizationBeginValueAmount']
             - dtPmts['scheduledSecuritizationEndValueAmount'])
            * (np.floor(dtPmts['currentDelinquencyStatus'] / 30) + 1)
        ))
        dtPmts['scheduledSecuritizationValueAmortization'] = amort

    if all(c in dtPmts.columns for c in
           ('scheduledSecuritizationBeginValueAmount', 'securitizationDiscountRate')):
        dtPmts['scheduledSecuritizationValueInterest'] = (
            dtPmts['scheduledSecuritizationBeginValueAmount']
            * dtPmts['securitizationDiscountRate'] / 12
        )

    # --- actualPrincipalCollectedAmount + principalPrepaid ---
    if all(c in dtPmts.columns for c in
           ('reportingPeriodSecuritizationValueAmount',
            'reportingPeriodEndActualSecuritizationAmount', 'chargedOffAmount')):
        dtPmts['actualPrincipalCollectedAmount'] = (
            dtPmts['reportingPeriodSecuritizationValueAmount']
            - dtPmts['reportingPeriodEndActualSecuritizationAmount']
            - dtPmts['chargedOffAmount']
        )

    prepay_inputs = ('chargedOffAmount', 'reportingPeriodSecuritizationValueAmount',
                     'reportingPeriodEndActualSecuritizationAmount', 'remainingTermNumber',
                     'actualPrincipalCollectedAmount',
                     'scheduledSecuritizationValueAmortization')
    if all(c in dtPmts.columns for c in prepay_inputs):
        prepay_mask = (
            (dtPmts['chargedOffAmount'].abs() < sens)
            & (dtPmts['reportingPeriodSecuritizationValueAmount'].abs() > sens)
            & (dtPmts['reportingPeriodEndActualSecuritizationAmount'].abs() < sens)
            & (dtPmts['remainingTermNumber'] > 1)
        )
        pp = pd.Series(0.0, index=dtPmts.index)
        pp = pp.where(~prepay_mask, (
            (dtPmts['actualPrincipalCollectedAmount']
             - dtPmts['scheduledSecuritizationValueAmortization']).clip(lower=0)
        ))
        dtPmts['principalPrepaid'] = pp

    # --- actualInterestCollectedAmount (bounded above by yield on beg-sec-value) ---
    int_inputs = ('totalActualAmountPaid', 'actualPrincipalCollectedAmount',
                  'reportingPeriodSecuritizationValueAmount', 'securitizationDiscountRate')
    if all(c in dtPmts.columns for c in int_inputs):
        i = (dtPmts['totalActualAmountPaid']
             - dtPmts['actualPrincipalCollectedAmount']).clip(lower=0)
        cap = (dtPmts['reportingPeriodSecuritizationValueAmount']
               * dtPmts['securitizationDiscountRate'] / 12)
        dtPmts['actualInterestCollectedAmount'] = np.minimum(i, cap)

    # --- otherPrincipalAdjustmentAmount (residual reconciliation) ---
    opa_inputs = ('totalActualAmountPaid', 'actualPrincipalCollectedAmount',
                  'actualInterestCollectedAmount')
    if all(c in dtPmts.columns for c in opa_inputs):
        dtPmts['otherPrincipalAdjustmentAmount'] = (
            dtPmts['totalActualAmountPaid']
            - dtPmts['actualPrincipalCollectedAmount']
            - dtPmts['actualInterestCollectedAmount']
        )

    # --- saleGainOrLoss ---
    if all(c in dtPmts.columns for c in
           ('liquidationProceedsAmount', 'contractResidualValue',
            'terminationIndicator', 'zeroBalanceCode')):
        sg = pd.Series(0.0, index=dtPmts.index)
        gain_mask = (
            dtPmts['terminationIndicator'].isin([1, 2])
            | dtPmts['zeroBalanceCode'].isin([1, 2])
        )
        sg = sg.where(~gain_mask, (
            dtPmts['liquidationProceedsAmount'] - dtPmts['contractResidualValue']
        ))
        dtPmts['saleGainOrLoss'] = sg

    # --- summaryDate, monthsFromCutoffDate, age, ageFromCutoffDate ---
    if 'securitizationKey' in dtPmts.columns:
        if 'reportingPeriodEndDate' in dtPmts.columns:
            max_end = dtPmts.groupby('securitizationKey')['reportingPeriodEndDate'].transform('max')
            dtPmts['summaryDate'] = (dtPmts['reportingPeriodEndDate'].eq(max_end)).astype(float)
        if 'reportingPeriodBeginDate' in dtPmts.columns:
            beg = pd.to_datetime(dtPmts['reportingPeriodBeginDate'], errors='coerce')
            min_beg = beg.groupby(dtPmts['securitizationKey']).transform('min')
            mfc = (
                (12 * beg.dt.year + beg.dt.month)
                - (12 * min_beg.dt.year + min_beg.dt.month)
            )
            dtPmts['monthsFromCutoffDate'] = mfc.astype(float)

    if all(c in dtPmts.columns for c in ('originalLeaseTermNumber', 'remainingTermNumber')):
        dtPmts['age'] = (
            dtPmts['originalLeaseTermNumber'] - dtPmts['remainingTermNumber']
        )
    if 'age' in dtPmts.columns and 'monthsFromCutoffDate' in dtPmts.columns:
        dtPmts['ageFromCutoffDate'] = dtPmts['age'] - dtPmts['monthsFromCutoffDate']

    # --- beginningBalanceAtCutoffDate ---
    if all(c in dtPmts.columns for c in
           ('monthsFromCutoffDate', 'assetNumber', 'securitizationKey',
            'reportingPeriodSecuritizationValueAmount')):
        at_cut = dtPmts.loc[
            dtPmts['monthsFromCutoffDate'] == 0,
            ['assetNumber', 'securitizationKey',
             'reportingPeriodSecuritizationValueAmount']
        ].rename(columns={
            'reportingPeriodSecuritizationValueAmount': 'beginningBalanceAtCutoffDate'
        })
        dtPmts = dtPmts.merge(at_cut, how='left', on=['assetNumber', 'securitizationKey'])

    # --- Consumer vs commercial credit score ---
    if 'lesseeCreditScore' in dtPmts.columns:
        score = dtPmts['lesseeCreditScore'].astype(float)
        low, high = const.validCreditScoreRange()
        type_col = dtPmts.get('lesseeCreditScoreType', pd.Series('', index=dtPmts.index)).astype(str)
        is_comm = type_col.str.contains('commercial', case=False, na=False)
        is_other = (
            (score < low) | (score > high)
            | type_col.str.contains('Unknown/Invalid', case=False, na=False)
            | type_col.str.contains('None', case=False, na=False)
        )
        consumer = score.where(~is_comm).where(~is_other)
        commercial = score.where(is_comm).where(~is_other)
        dtPmts['consumerCreditScore'] = consumer
        dtPmts['commercialCreditScore'] = commercial

    # --- LTV (acquisitionCost / vehicleValueAmount) ---
    if all(c in dtPmts.columns for c in ('acquisitionCost', 'vehicleValueAmount')):
        ltv = dtPmts['acquisitionCost'] / dtPmts['vehicleValueAmount'].replace(0, np.nan)
        ltv = ltv.replace([np.inf, -np.inf], np.nan)
        dtPmts['loanToValueRatio'] = ltv

    # --- Synthetic originalLoanAmount, originalInterestRatePercentage ---
    if all(c in dtPmts.columns for c in
           ('originalLeaseTermNumber', 'reportingPeriodScheduledPaymentAmount',
            'contractResidualValue')):
        dtPmts['originalLoanAmount'] = (
            dtPmts['originalLeaseTermNumber'] * dtPmts['reportingPeriodScheduledPaymentAmount']
            + dtPmts['contractResidualValue']
        )

    if all(c in dtPmts.columns for c in
           ('originalLeaseTermNumber', 'reportingPeriodScheduledPaymentAmount',
            'acquisitionCost', 'contractResidualValue')):
        # npf.rate is a Newton solver; it can return NaN for some rows when
        # cashflows don't imply a positive rate. Clip to non-negative monthly,
        # then annualize.
        monthly = npf.rate(
            dtPmts['originalLeaseTermNumber'],
            dtPmts['reportingPeriodScheduledPaymentAmount'],
            -dtPmts['acquisitionCost'],
            dtPmts['contractResidualValue'],
            1,
        )
        annual = 12 * np.maximum(monthly, 0)
        dtPmts['originalInterestRatePercentage'] = annual
        dtPmts['nextInterestRatePercentage'] = annual

    # --- Vintage ---
    if 'originationDate' in dtPmts.columns:
        dtPmts['vintage'] = pd.to_datetime(
            dtPmts['originationDate'], errors='coerce'
        ).dt.year

    # --- Prime tier ---
    if 'consumerCreditScore' in dtPmts.columns:
        score = dtPmts['consumerCreditScore']
        prime = pd.Series(0.0, index=dtPmts.index)
        for lo, hi, code in const.primeTiers():
            mask = score.notna() & (score > lo) & (score <= hi)
            prime = prime.where(~mask, code)
        dtPmts['primeIndicator'] = prime

    # --- Net losses ---
    if all(c in dtPmts.columns for c in ('chargedOffAmount', 'repurchaseAmount')):
        dtPmts['netLosses'] = (
            (dtPmts['chargedOffAmount'] - dtPmts['repurchaseAmount']).clip(lower=0).fillna(0)
        )

    # --- Region ---
    if 'lesseeGeographicLocation' in dtPmts.columns:
        try:
            dtRegion = read_dict('states.csv')
        except FileNotFoundError:
            log.warning("Inputs/states.csv missing; skipping region lookup.")
        else:
            dtPmts = dtPmts.merge(
                dtRegion, how='left', left_on='lesseeGeographicLocation', right_on='state'
            )
            if 'state' in dtPmts.columns:
                dtPmts = dtPmts.drop(columns=['state'])

    return dtPmts


# ---------------------------------------------------------------------------
# Step 3: vetting
# ---------------------------------------------------------------------------

def cashflow_vetting(dtP: pd.DataFrame) -> pd.DataFrame:
    """Per-month cashflow rollup for a single securitization slice (lease shape)."""
    cols = ['reportingPeriodSecuritizationValueAmount',
            'actualPrincipalCollectedAmount', 'chargedOffAmount',
            'otherPrincipalAdjustmentAmount',
            'reportingPeriodEndActualSecuritizationAmount',
            'actualInterestCollectedAmount', 'recoveredAmount',
            'liquidationProceedsAmount']
    present = _present(cols, dtP)
    if not present or 'reportingPeriodBeginDate' not in dtP.columns:
        return pd.DataFrame()
    return dtP.groupby('reportingPeriodBeginDate')[present].sum()


def _vet_one(dtP: pd.DataFrame) -> dict:
    """Per-securitization error counts on a sorted single-sec slice."""
    sens = const.minSens()
    out = {c: 0 for c in const.rawCols()}

    out['Count'] = int(dtP['assetNumber'].nunique()) if 'assetNumber' in dtP.columns else 0

    beg_col = 'reportingPeriodSecuritizationValueAmount'
    end_col = 'reportingPeriodEndActualSecuritizationAmount'
    if all(c in dtP.columns for c in (beg_col, 'reportingPeriodBeginDate')):
        beg_dates = dtP['reportingPeriodBeginDate']
        min_beg = beg_dates.min()
        out['OpenBal'] = float(dtP.loc[beg_dates == min_beg, beg_col].sum())
        out['StartMonth'] = min_beg
        out['EndMonth'] = beg_dates.max()

    if 'reportingPeriodEndDate' in dtP.columns:
        end_dates = dtP['reportingPeriodEndDate']
        expected = pd.date_range(start=end_dates.min(), end=end_dates.max(), freq='M')
        present_months = set(end_dates.dropna().unique())
        out['MissingMonths'] = int(sum(1 for d in expected if d not in present_months))

    if all(c in dtP.columns for c in (beg_col, end_col, 'assetNumber', 'securitizationKey')):
        beg = dtP[beg_col].to_numpy()
        end = dtP[end_col].to_numpy()
        same_asset = dtP['assetNumber'].to_numpy()[1:] == dtP['assetNumber'].to_numpy()[:-1]
        same_sec = dtP['securitizationKey'].to_numpy()[1:] == dtP['securitizationKey'].to_numpy()[:-1]
        contiguous = same_asset & same_sec
        out['Walk'] = int(((beg[1:] != end[:-1]) & contiguous).sum())
        out['IncrBal'] = int(((beg[:-1] < beg[1:]) & contiguous).sum())

    pay_inputs = (beg_col, end_col, 'actualPrincipalCollectedAmount',
                  'chargedOffAmount', 'otherPrincipalAdjustmentAmount')
    if all(c in dtP.columns for c in pay_inputs):
        diff = (
            (dtP[beg_col] - dtP[end_col])
            - (dtP['actualPrincipalCollectedAmount']
               + dtP['chargedOffAmount']
               + dtP['otherPrincipalAdjustmentAmount'])
        )
        out['Pmts'] = int((diff.abs() > sens).sum())

    if all(c in dtP.columns for c in
           ('reportingPeriodBeginDate', end_col, 'assetNumber')):
        secMonths = np.sort(dtP['reportingPeriodBeginDate'].dropna().unique())
        missing = extra = 0
        for i in range(len(secMonths) - 1):
            this_month = dtP['reportingPeriodBeginDate'] == secMonths[i]
            next_month = dtP['reportingPeriodBeginDate'] == secMonths[i + 1]
            this_loans = set(dtP.loc[
                this_month & (dtP[end_col].abs() > sens), 'assetNumber',
            ].unique())
            next_loans = set(dtP.loc[next_month, 'assetNumber'].unique())
            missing += len(this_loans - next_loans)
            extra += len(next_loans - this_loans)
        out['Missing'] = missing
        out['Extra'] = extra

    if all(c in dtP.columns for c in
           ('assetNumber', 'reportingPeriodBeginDate', 'chargedOffAmount')):
        co_rows = dtP.loc[dtP['chargedOffAmount'] > sens,
                          ['assetNumber', 'reportingPeriodBeginDate']]
        if not co_rows.empty:
            co_first = (
                co_rows.groupby('assetNumber')['reportingPeriodBeginDate']
                       .min()
                       .rename('_co_date')
            )
            joined = (
                dtP[['assetNumber', 'reportingPeriodBeginDate']]
                .merge(co_first, left_on='assetNumber', right_index=True, how='inner')
            )
            out['COExtra'] = int(
                (joined['reportingPeriodBeginDate'] > joined['_co_date']).sum()
            )

    dup_keys = _present(
        ['assetNumber', 'reportingPeriodBeginDate', 'securitizationKey'], dtP
    )
    if dup_keys:
        out['Dupes'] = int(dtP.duplicated(subset=dup_keys, keep=False).sum())

    if beg_col in dtP.columns:
        out['NegOpenBal'] = int((dtP[beg_col] < 0).sum())
    if end_col in dtP.columns:
        out['NegCloseBal'] = int((dtP[end_col] < 0).sum())

    rate_cols = _present(const.rateFields(), dtP)
    if rate_cols:
        out['RateNeg'] = int((dtP[rate_cols] < 0).sum().sum())
        out['RatePos'] = int((dtP[rate_cols] > 1).sum().sum())

    int_cols = _present(const.integerFields(), dtP)
    if int_cols:
        out['Integer'] = int(
            (dtP[int_cols].mod(1, axis=0, fill_value=0) != 0).sum().sum()
        )

    if 'chargedOffAmount' in dtP.columns:
        co = dtP['chargedOffAmount']
        out['NegCO'] = int((co < 0).sum())
        if beg_col in dtP.columns:
            beg = dtP[beg_col]
            mask_nz = co.abs() > sens
            out['PartialCO'] = int(((co < beg) & mask_nz).sum())
            out['GreaterCO'] = int(((co > beg) & mask_nz).sum())

    # Lease parser has liquidationProceedsAmount (~= repo) and no separate recoveredAmount on raw data;
    # use those for NegRepo/NegRecov to mirror the loan-side intent.
    if 'liquidationProceedsAmount' in dtP.columns:
        out['NegRepo'] = int((dtP['liquidationProceedsAmount'] < 0).sum())
    if 'recoveredAmount' in dtP.columns:
        out['NegRecov'] = int((dtP['recoveredAmount'] < 0).sum())

    return out


def data_vetting(dtPmts: pd.DataFrame):
    """Per-securitization error/description rollup. See loan version for shape."""
    if 'securitizationKey' not in dtPmts.columns:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    sort_cols = _present(['securitizationKey', 'assetNumber', 'reportingPeriodBeginDate'], dtPmts)
    dtPmts = dtPmts.sort_values(sort_cols).reset_index(drop=True) if sort_cols else dtPmts

    sec_keys = sorted(dtPmts['securitizationKey'].dropna().unique())
    error_records: dict[str, dict] = {}
    cf_frames: list[pd.DataFrame] = []
    desc_num_frames: list[pd.DataFrame] = []
    desc_str_frames: list[pd.DataFrame] = []
    num_fields = (const.decimalFields() + const.integerFields()
                  + const.rateFields() + const.dateFields())
    str_fields = const.stringFields()

    for s in sec_keys:
        log.info("Vetting securitization: %s", s)
        dtP = dtPmts.loc[dtPmts['securitizationKey'] == s].reset_index(drop=True)
        error_records[s] = _vet_one(dtP)

        cf = cashflow_vetting(dtP).sum(axis=0)
        cf.name = s
        cf_frames.append(cf.to_frame())

        present_num = _present(num_fields, dtP)
        if present_num:
            num_part = dtP[present_num].describe(include=[np.number]).transpose()
            extras = pd.concat([
                dtP[present_num].isna().sum().rename('nans'),
                (dtP[present_num] == 0).sum().rename('zeros'),
                (dtP[_present(const.rateFields(), dtP)] > 1).sum().rename('rate>1'),
                (dtP[_present(const.rateFields(), dtP)] < 0).sum().rename('rate<0'),
                (dtP[_present(const.integerFields(), dtP)]
                    .mod(1, axis=0, fill_value=0) != 0).sum().rename('non-int'),
            ], axis=1)
            num_part = num_part.join(extras, how='left')
            num_part.columns = [f"{s} {c}" for c in num_part.columns]
            desc_num_frames.append(num_part)

        present_str = _present(str_fields, dtP)
        if present_str:
            str_part = dtP[present_str].describe(exclude=[np.number]).transpose()
            str_part['nans'] = dtP[present_str].isna().sum()
            str_part.columns = [f"{s} {c}" for c in str_part.columns]
            desc_str_frames.append(str_part)

    dtErrors = pd.DataFrame.from_dict(error_records, orient='columns')
    dtErrors = dtErrors.reindex(const.rawCols())
    if cf_frames:
        dtCF = pd.concat(cf_frames, axis=1)
        dtCF = dtCF.reindex(columns=sec_keys)
        dtErrors = pd.concat([dtErrors, dtCF], axis=0)

    dtDescNum = pd.concat(desc_num_frames, axis=1) if desc_num_frames else pd.DataFrame()
    dtDescStr = pd.concat(desc_str_frames, axis=1) if desc_str_frames else pd.DataFrame()
    return dtErrors, dtDescNum, dtDescStr


# ---------------------------------------------------------------------------
# Optional helpers (preserved from the original)
# ---------------------------------------------------------------------------

def fit_reporting_model(dtPmts: pd.DataFrame) -> pd.DataFrame:
    """Rename lease-side columns to their auto-loan equivalents so the downstream
    reporting logic (which assumes the loan schema) can ingest lease frames."""
    return dtPmts.rename(columns={
        'chargedOffAmount': 'chargedoffPrincipalAmount',
        'liquidationProceedsAmount': 'repossessedProceedsAmount',
        'reportingPeriodEndingActualBalanceAmount': 'reportingPeriodEndingLeaseBalanceAmount',
        'reportingPeriodSecuritizationValueAmount': 'reportingPeriodBeginningLoanBalanceAmount',
        'reportingPeriodEndActualSecuritizationAmount': 'reportingPeriodActualEndBalanceAmount',
        'coLesseePresentIndicator': 'coObligorIndicator',
        'paidThroughDate': 'interestPaidThroughDate',
        'reportingPeriodBeginDate': 'reportingPeriodBeginningDate',
        'reportingPeriodEndDate': 'reportingPeriodEndingDate',
        'scheduledTerminationDate': 'loanMaturityDate',
        'gracePeriod': 'gracePeriodNumber',
        'lesseeCreditScore': 'obligorCreditScore',
        'lesseeEmploymentVerificationCode': 'obligorEmploymentVerificationCode',
        'lesseeIncomeVerificationLevelCode': 'obligorIncomeVerificationLevelCode',
        'originalLeaseTermNumber': 'originalLoanTerm',
        'remainingTermNumber': 'remainingTermToMaturityNumber',
        'lesseeCreditScoreType': 'obligorCreditScoreType',
        'lesseeGeographicLocation': 'obligorGeographicLocation',
        'primaryLeaseServicerName': 'primaryLoanServicerName',
    })


def describe_raw_data(dtPmts: pd.DataFrame) -> pd.DataFrame:
    """Per-securitization description of boolean+string fields."""
    fields = _present(const.booleanFields() + const.stringFields(), dtPmts)
    if 'securitizationKey' not in dtPmts.columns or not fields:
        return pd.DataFrame()
    frames = []
    for s in sorted(dtPmts['securitizationKey'].dropna().unique()):
        sec = dtPmts.loc[dtPmts['securitizationKey'] == s, fields]
        try:
            desc = sec.describe(exclude=[np.number]).transpose()
        except Exception:
            desc = pd.DataFrame(index=fields)
        desc['vals'] = [list(sec[f].dropna().unique()) for f in desc.index]
        desc['nan'] = [int(sec[f].isna().sum()) for f in desc.index]
        desc.index = [f"{s} {idx}" for idx in desc.index]
        frames.append(desc)
    return pd.concat(frames, axis=0) if frames else pd.DataFrame()
