"""Clean and enrich raw auto-loan ABS-EE data into a normalized payments DataFrame.

Public surface (unchanged from the 2017 version):
    const.<field-list>()          # schema-level column lists
    clean_ald_files(df)           # normalize dates/numerics/codes/dedup
    append_calc_fields(df)        # derive age, LTV, monthsDelinquent, prime tier, etc.
    data_vetting(df)              # (errors, descNum, descStr) per securitization
    cashflow_vetting(df)          # per-month cashflow rollup

Differences vs. the original:
- Imports cleanly (no broken ``def main(...)`` scratchpad at the bottom).
- All ``df['col'].iloc[idx] = v`` chained assignments are now ``df.loc[mask, 'col'] = v``,
  which is safe under pandas 2.x copy-on-write.
- Column lookups are defensive: any field absent from the input is skipped, not
  KeyError'd. The optional ``servicerAdvancedAmount`` / ``servicingFlatFeeAmount``
  / ``otherServicerFeeRetainedByServicer`` / ``originalInterestOnlyTermNumber``
  fields can now be absent.
- Rate scaling no longer divides every value in a security by 100 when *some*
  values look percent-formatted; only values >1 are scaled. Remaining >1
  outliers are set to NaN (was: 0, which silently destroyed real outliers).
- Unmapped vehicle-manufacturer names fall back to the raw name instead of
  becoming ``'N/A'``.
- ``data_vetting`` sorts the slice before its row-walking check (was unsorted
  and producing meaningless counts), uses ``.duplicated()`` instead of the
  removed ``MultiIndex.get_duplicates``, replaces the O(N²) charge-off check
  with a groupby/merge, and uses ``.at`` instead of the removed ``.ix``.
- ``clean_ald_files`` copies its input rather than mutating it.
- Lookup CSVs are resolved relative to this file, not the process cwd.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
INPUTS = ROOT / "Inputs"
log = logging.getLogger("absee.autoloan")


class const:
    @staticmethod
    def booleanFields():
        return ['assetAddedIndicator', 'assetSubjectDemandIndicator', 'coObligorIndicator',
                'reportingPeriodModificationIndicator', 'repossessedIndicator', 'underwritingIndicator']

    @staticmethod
    def dateFields():
        return ['originalFirstPaymentDate', 'originationDate', 'reportingPeriodBeginningDate',
                'reportingPeriodEndingDate', 'zeroBalanceEffectiveDate']

    @staticmethod
    def debugFieldsClean():
        return ['reportingPeriodBeginningDate', 'assetNumber', 'securitizationKey',
                'reportingPeriodBeginningLoanBalanceAmount', 'actualPrincipalCollectedAmount',
                'chargedoffPrincipalAmount', 'otherPrincipalAdjustmentAmount',
                'reportingPeriodActualEndBalanceAmount', 'actualInterestCollectedAmount',
                'recoveredAmount', 'repossessedProceedsAmount', 'principalPrepaid', 'dueAmount',
                'scheduledPrincipalAmount', 'nextReportingPeriodPaymentAmountDue',
                'currentDelinquencyStatus', 'totalActualAmountPaid']

    @staticmethod
    def debugFieldsRaw():
        return ['reportingPeriodBeginningDate', 'assetNumber', 'securitizationKey',
                'reportingPeriodBeginningLoanBalanceAmount', 'actualPrincipalCollectedAmount',
                'chargedoffPrincipalAmount', 'otherPrincipalAdjustmentAmount',
                'reportingPeriodActualEndBalanceAmount', 'actualInterestCollectedAmount',
                'recoveredAmount', 'repossessedProceedsAmount', 'scheduledPrincipalAmount',
                'nextReportingPeriodPaymentAmountDue', 'currentDelinquencyStatus',
                'totalActualAmountPaid']

    @staticmethod
    def decimalFields():
        return ['actualInterestCollectedAmount', 'actualOtherCollectedAmount',
                'actualPrincipalCollectedAmount', 'chargedoffPrincipalAmount',
                'originalLoanAmount', 'otherAssessedUncollectedServicerFeeAmount',
                'otherPrincipalAdjustmentAmount', 'recoveredAmount',
                'reportingPeriodActualEndBalanceAmount',
                'reportingPeriodBeginningLoanBalanceAmount',
                'reportingPeriodScheduledPaymentAmount', 'totalActualAmountPaid',
                'nextReportingPeriodPaymentAmountDue', 'repossessedProceedsAmount',
                'scheduledInterestAmount', 'scheduledPrincipalAmount', 'vehicleValueAmount',
                'servicerAdvancedAmount', 'servicingFlatFeeAmount',
                'otherServicerFeeRetainedByServicer']

    @staticmethod
    def integerFields():
        return ['currentDelinquencyStatus', 'gracePeriodNumber', 'interestCalculationTypeCode',
                'obligorCreditScore', 'obligorEmploymentVerificationCode',
                'obligorIncomeVerificationLevelCode', 'originalInterestRateTypeCode',
                'originalLoanTerm', 'paymentExtendedNumber', 'paymentTypeCode',
                'remainingTermToMaturityNumber', 'servicingAdvanceMethodCode',
                'vehicleModelYear', 'vehicleNewUsedCode', 'vehicleTypeCode',
                'vehicleValueSourceCode', 'zeroBalanceCode', 'originalInterestOnlyTermNumber']

    @staticmethod
    def listFields():
        return ['modificationTypeCode', 'subvented']

    @staticmethod
    def rateFields():
        return ['nextInterestRatePercentage', 'originalInterestRatePercentage',
                'paymentToIncomePercentage', 'reportingPeriodInterestRatePercentage',
                'servicingFeePercentage']

    @staticmethod
    def stringFields():
        return ['assetNumber', 'assetTypeNumber', 'obligorCreditScoreType',
                'obligorGeographicLocation', 'originatorName', 'primaryLoanServicerName',
                'securitizationKey', 'vehicleManufacturerName', 'vehicleModelName']

    @staticmethod
    def rawCols():
        return ['Count', 'OpenBal', 'StartMonth', 'EndMonth', 'MissingMonths', 'Walk',
                'IncrBal', 'Pmts', 'Missing', 'Extra', 'COExtra', 'Dupes', 'NegOpenBal',
                'NegCloseBal', 'RateNeg', 'RatePos', 'Integer', 'NegCO', 'PartialCO',
                'GreaterCO', 'NegRepo', 'NegRecov']

    @staticmethod
    def minSens(): return .001
    @staticmethod
    def divInd(): return .4
    @staticmethod
    def divMax(): return 5
    # Credit-score tiering for primeIndicator (lower-exclusive, upper-inclusive, code).
    # 0 = other/unknown (kept as default), 1..4 = sub/near/prime/superprime.
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
    """Intersect a column list with what's actually on df."""
    return [c for c in cols if c in df.columns]


# ---------------------------------------------------------------------------
# Step 1: clean
# ---------------------------------------------------------------------------

def clean_ald_files(dtPmts: pd.DataFrame) -> pd.DataFrame:
    """Normalize raw ABS-EE auto-loan data.

    Cleans dates, coerces numerics, fills key NaN-as-0 fields, dedups asset-month
    rows, normalizes vehicle-manufacturer names against ``Inputs/manus.csv``, and
    fixes rate scaling (percent vs decimal). Does not mutate the caller.
    """
    dtPmts = dtPmts.copy()

    # --- Dates ---
    date_formats = {
        'reportingPeriodBeginningDate': '%m-%d-%Y',
        'reportingPeriodEndingDate': '%m-%d-%Y',
        'originationDate': '%m/%Y',
        'originalFirstPaymentDate': '%m/%Y',
        'zeroBalanceEffectiveDate': '%m/%Y',
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
    # brand-new pools where no charge-off / repo / recovery has occurred yet).
    zero_default = [
        'chargedoffPrincipalAmount', 'repossessedProceedsAmount',
        'recoveredAmount', 'otherPrincipalAdjustmentAmount',
    ]
    for c in zero_default:
        if c in dtPmts.columns:
            dtPmts[c] = dtPmts[c].fillna(0)
        else:
            dtPmts[c] = 0.0

    # --- Fill-if-present: zero is also fine here, but don't synthesize the
    # column when absent (column absence is a real signal for these).
    fill_if_present = [
        'obligorIncomeVerificationLevelCode', 'obligorEmploymentVerificationCode',
        'obligorCreditScore', 'currentDelinquencyStatus',
    ]
    for c in _present(fill_if_present, dtPmts):
        dtPmts[c] = dtPmts[c].fillna(0)

    # --- Dedup CarMax u-vs-b style overlaps ---
    dedup_keys = _present(
        ['assetNumber', 'reportingPeriodBeginningDate', 'securitizationKey'], dtPmts
    )
    if dedup_keys:
        before = len(dtPmts)
        dtPmts = dtPmts.drop_duplicates(subset=dedup_keys, keep='last').reset_index(drop=True)
        if len(dtPmts) != before:
            log.info("Dropped %d duplicate rows on %s", before - len(dtPmts), dedup_keys)

    # --- Vehicle manufacturer name normalization (with raw fallback) ---
    if 'vehicleManufacturerName' in dtPmts.columns:
        try:
            dtManus = read_dict('manus.csv')
        except FileNotFoundError:
            log.warning("Inputs/manus.csv missing; leaving vehicleManufacturerName raw.")
        else:
            dtPmts = dtPmts.merge(
                dtManus, how='left', left_on='vehicleManufacturerName', right_on='old'
            )
            # Fall back to the original name when the dictionary doesn't map it.
            dtPmts['vehicleManufacturerName'] = (
                dtPmts['new'].fillna(dtPmts['vehicleManufacturerName'])
            )
            dtPmts = dtPmts.drop(columns=[c for c in ('old', 'new') if c in dtPmts.columns])
            dtPmts['vehicleManufacturerName'] = dtPmts['vehicleManufacturerName'].fillna('N/A')

    # --- Rate scaling per securitization ---
    # If the bulk of a column's values exceed 1 (percent form), divide *only those*
    # by 100 — leaving already-decimal values alone. Lingering >1 values after
    # scaling are bad data and become NaN (was: 0, which destroyed signal).
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
    """Add derived fields: balances tie-out, age, LTV, monthsDelinquent, prime tier, etc."""
    dtPmts = dtPmts.copy()
    sens = const.minSens()

    # --- Backfill missing beginning balances ---
    beg_col = 'reportingPeriodBeginningLoanBalanceAmount'
    end_col = 'reportingPeriodActualEndBalanceAmount'
    if beg_col in dtPmts.columns:
        beg_nan = dtPmts[beg_col].isna()
        if beg_nan.any():
            recover = ['reportingPeriodActualEndBalanceAmount', 'otherPrincipalAdjustmentAmount',
                       'chargedoffPrincipalAmount', 'actualPrincipalCollectedAmount']
            dtPmts.loc[beg_nan, beg_col] = (
                dtPmts.loc[beg_nan, _present(recover, dtPmts)].sum(axis=1)
            )

    # --- Charge-off / repo / recovery adjustments to make balances tick ---
    if all(c in dtPmts.columns for c in
           ('chargedoffPrincipalAmount', 'repossessedProceedsAmount', 'recoveredAmount', beg_col)):
        co = dtPmts['chargedoffPrincipalAmount']
        repo = dtPmts['repossessedProceedsAmount']
        recov = dtPmts['recoveredAmount']
        beg = dtPmts[beg_col]
        adj_mask = (repo.abs() > sens) | (co.abs() > sens) | (recov.abs() > sens)

        # If coAmt = openBal, recovAmt = 0, repoAmt > 0: recovAmt += repoAmt
        c1 = (
            ((co - beg).abs() < sens)
            & (recov.abs() < sens)
            & (repo.abs() > sens)
            & adj_mask
        )
        dtPmts.loc[c1, 'recoveredAmount'] = (
            dtPmts.loc[c1, 'recoveredAmount'] + dtPmts.loc[c1, 'repossessedProceedsAmount']
        )

        # If repoAmt + coAmt + recovAmt = openBal: roll it all into coAmt and zero out endBal
        c2 = (
            ((repo + co + recov - beg).abs() < sens)
            & adj_mask
        )
        dtPmts.loc[c2, 'chargedoffPrincipalAmount'] = (
            dtPmts.loc[c2, 'chargedoffPrincipalAmount']
            + dtPmts.loc[c2, 'repossessedProceedsAmount']
            + dtPmts.loc[c2, 'recoveredAmount']
        )
        dtPmts.loc[c2, 'recoveredAmount'] = (
            dtPmts.loc[c2, 'recoveredAmount'] + dtPmts.loc[c2, 'repossessedProceedsAmount']
        )
        if end_col in dtPmts.columns:
            dtPmts.loc[c2, end_col] = 0

    # --- Make principal balances tick exactly via otherPrincipalAdjustmentAmount ---
    needed = [beg_col, end_col, 'actualPrincipalCollectedAmount',
              'chargedoffPrincipalAmount', 'otherPrincipalAdjustmentAmount']
    if all(c in dtPmts.columns for c in needed):
        prin_offset = (
            (dtPmts[beg_col] - dtPmts[end_col])
            - (dtPmts['actualPrincipalCollectedAmount']
               + dtPmts['chargedoffPrincipalAmount']
               + dtPmts['otherPrincipalAdjustmentAmount'])
        )
        dtPmts['otherPrincipalAdjustmentAmount'] = (
            dtPmts['otherPrincipalAdjustmentAmount'] + prin_offset
        )
        log.info("Reconciled principal walk via otherPrincipalAdjustmentAmount")

    # --- summaryDate (latest reporting month per securitization) & monthsFromCutoffDate ---
    if 'securitizationKey' in dtPmts.columns:
        if 'reportingPeriodEndingDate' in dtPmts.columns:
            sec_max_end = dtPmts.groupby('securitizationKey')[
                'reportingPeriodEndingDate'
            ].transform('max')
            dtPmts['summaryDate'] = (
                dtPmts['reportingPeriodEndingDate'].eq(sec_max_end).astype(float)
            )

        if 'reportingPeriodBeginningDate' in dtPmts.columns:
            beg_dt = pd.to_datetime(dtPmts['reportingPeriodBeginningDate'], errors='coerce')
            sec_min_beg = beg_dt.groupby(dtPmts['securitizationKey']).transform('min')
            mfc = (12 * beg_dt.dt.year + beg_dt.dt.month) - (12 * sec_min_beg.dt.year + sec_min_beg.dt.month)
            dtPmts['monthsFromCutoffDate'] = mfc.astype(float)

    # --- age (months between beginning date and origination date) ---
    if all(c in dtPmts.columns for c in ('reportingPeriodBeginningDate', 'originationDate')):
        beg = pd.to_datetime(dtPmts['reportingPeriodBeginningDate'], errors='coerce')
        orig = pd.to_datetime(dtPmts['originationDate'], errors='coerce')
        dtPmts['age'] = (12 * beg.dt.year + beg.dt.month) - (12 * orig.dt.year + orig.dt.month)

    if 'monthsFromCutoffDate' in dtPmts.columns and 'age' in dtPmts.columns:
        dtPmts['ageFromCutoffDate'] = dtPmts['age'] - dtPmts['monthsFromCutoffDate']

    # --- beginningBalanceAtCutoffDate: each asset's beg balance at monthsFromCutoffDate==0 ---
    if all(c in dtPmts.columns for c in ('monthsFromCutoffDate', 'assetNumber', 'securitizationKey', beg_col)):
        at_cutoff = dtPmts.loc[
            dtPmts['monthsFromCutoffDate'] == 0,
            ['assetNumber', 'securitizationKey', beg_col]
        ].rename(columns={beg_col: 'beginningBalanceAtCutoffDate'})
        dtPmts = dtPmts.merge(at_cutoff, how='left', on=['assetNumber', 'securitizationKey'])

    # --- Split credit score into consumer vs commercial ---
    if 'obligorCreditScore' in dtPmts.columns:
        score = dtPmts['obligorCreditScore'].astype(float)
        low, high = const.validCreditScoreRange()
        type_col = dtPmts.get('obligorCreditScoreType', pd.Series('', index=dtPmts.index)).astype(str)
        is_comm = type_col.str.contains('commercial', case=False, na=False)
        is_other = (
            (score < low) | (score > high)
            | type_col.str.contains('Unknown/Invalid', case=False, na=False)
            | type_col.str.contains('None', case=False, na=False)
        )
        consumer = score.where(~is_comm)
        commercial = score.where(is_comm)
        consumer = consumer.where(~is_other)
        commercial = commercial.where(~is_other)
        dtPmts['consumerCreditScore'] = consumer
        dtPmts['commercialCreditScore'] = commercial

    # --- Loan-to-value (cap at 2x; flag inf as NaN) ---
    if all(c in dtPmts.columns for c in ('originalLoanAmount', 'vehicleValueAmount')):
        ltv = dtPmts['originalLoanAmount'] / dtPmts['vehicleValueAmount'].replace(0, np.nan)
        ltv = ltv.replace([np.inf, -np.inf], np.nan)
        ltv = ltv.where(ltv <= 2)
        dtPmts['loanToValueRatio'] = ltv

    # --- Vintage ---
    if 'originationDate' in dtPmts.columns:
        dtPmts['vintage'] = pd.to_datetime(dtPmts['originationDate'], errors='coerce').dt.year

    # --- monthsDelinquent: 0..4 normal, 5 = charge-off, 6 = paid in full this period ---
    if 'currentDelinquencyStatus' in dtPmts.columns:
        md = np.ceil(dtPmts['currentDelinquencyStatus'].astype(float) / 30).clip(upper=4)
        if 'chargedoffPrincipalAmount' in dtPmts.columns:
            md = md.where(~(dtPmts['chargedoffPrincipalAmount'] > 0), 5)
        prepay_inputs = ['otherPrincipalAdjustmentAmount', 'actualPrincipalCollectedAmount',
                         beg_col, end_col]
        if all(c in dtPmts.columns for c in prepay_inputs):
            prepay = (
                (dtPmts['otherPrincipalAdjustmentAmount'] + dtPmts['actualPrincipalCollectedAmount']
                 >= dtPmts[beg_col])
                & (dtPmts[end_col] <= .05)
            )
            md = md.where(~prepay, 6)
        dtPmts['monthsDelinquent'] = md

    # --- dueAmount + principalPrepaid: join previous month's "next-period due" ---
    dueAmount_inputs = ['assetNumber', 'securitizationKey',
                        'reportingPeriodEndingDate', 'reportingPeriodBeginningDate',
                        'nextReportingPeriodPaymentAmountDue']
    if all(c in dtPmts.columns for c in dueAmount_inputs):
        nxt = dtPmts[['assetNumber', 'securitizationKey',
                       'reportingPeriodEndingDate', 'nextReportingPeriodPaymentAmountDue']].copy()
        nxt['_join_key'] = (
            pd.to_datetime(nxt['reportingPeriodEndingDate'], errors='coerce')
              + pd.DateOffset(days=1)
        ).dt.strftime('%m-%d-%Y')
        nxt = nxt.drop(columns=['reportingPeriodEndingDate'])
        nxt = nxt.rename(columns={'nextReportingPeriodPaymentAmountDue': '_prev_due'})

        dtPmts['_join_key'] = pd.to_datetime(
            dtPmts['reportingPeriodBeginningDate'], errors='coerce'
        ).dt.strftime('%m-%d-%Y')
        dtPmts = dtPmts.merge(
            nxt, how='left', on=['assetNumber', 'securitizationKey', '_join_key']
        )
        dtPmts = dtPmts.drop(columns=['_join_key'])
        dtPmts['dueAmount'] = pd.to_numeric(dtPmts.pop('_prev_due'), errors='coerce')

        # Fallback: when no prior row exists, estimate from scheduled fields + delinquency
        fallback_inputs = ['scheduledInterestAmount', 'scheduledPrincipalAmount',
                           'currentDelinquencyStatus']
        if all(c in dtPmts.columns for c in fallback_inputs):
            nan = dtPmts['dueAmount'].isna()
            dtPmts.loc[nan, 'dueAmount'] = (
                dtPmts.loc[nan, ['scheduledInterestAmount', 'scheduledPrincipalAmount']].sum(axis=1)
                * (np.floor(dtPmts.loc[nan, 'currentDelinquencyStatus'].astype(float) / 30) + 1)
            )

        # principalPrepaid = max(P+I collected - dueAmount, 0), zeroed for any charge-off row.
        # Use sum(skipna=True) so a missing actualInterestCollectedAmount is treated
        # as 0 rather than poisoning the row.
        if all(c in dtPmts.columns for c in
               ('actualPrincipalCollectedAmount', 'actualInterestCollectedAmount',
                'chargedoffPrincipalAmount')):
            collected = dtPmts[
                ['actualPrincipalCollectedAmount', 'actualInterestCollectedAmount']
            ].sum(axis=1)
            pp = (collected - dtPmts['dueAmount']).clip(lower=0)
            pp = pp.where(~(dtPmts['chargedoffPrincipalAmount'].abs() > sens), 0)
            dtPmts['principalPrepaid'] = pp

    # --- Prime tier from consumerCreditScore ---
    if 'consumerCreditScore' in dtPmts.columns:
        score = dtPmts['consumerCreditScore']
        prime = pd.Series(0.0, index=dtPmts.index)
        for lo, hi, code in const.primeTiers():
            mask = score.notna() & (score > lo) & (score <= hi)
            prime = prime.where(~mask, code)
        dtPmts['primeIndicator'] = prime

    # --- Net losses ---
    if all(c in dtPmts.columns for c in ('chargedoffPrincipalAmount', 'recoveredAmount')):
        dtPmts['netLosses'] = (
            (dtPmts['chargedoffPrincipalAmount'] - dtPmts['recoveredAmount']).clip(lower=0).fillna(0)
        )

    # --- Region lookup ---
    if 'obligorGeographicLocation' in dtPmts.columns:
        try:
            dtRegion = read_dict('states.csv')
        except FileNotFoundError:
            log.warning("Inputs/states.csv missing; skipping region lookup.")
        else:
            dtPmts = dtPmts.merge(
                dtRegion, how='left', left_on='obligorGeographicLocation', right_on='state'
            )
            if 'state' in dtPmts.columns:
                dtPmts = dtPmts.drop(columns=['state'])

    return dtPmts


# ---------------------------------------------------------------------------
# Step 3: vetting
# ---------------------------------------------------------------------------

def cashflow_vetting(dtP: pd.DataFrame) -> pd.DataFrame:
    """Per-month cashflow rollup for a single securitization slice."""
    cols = ['reportingPeriodBeginningLoanBalanceAmount', 'actualPrincipalCollectedAmount',
            'chargedoffPrincipalAmount', 'otherPrincipalAdjustmentAmount',
            'reportingPeriodActualEndBalanceAmount', 'actualInterestCollectedAmount',
            'recoveredAmount', 'repossessedProceedsAmount']
    present = _present(cols, dtP)
    if not present or 'reportingPeriodBeginningDate' not in dtP.columns:
        return pd.DataFrame()
    return dtP.groupby('reportingPeriodBeginningDate')[present].sum()


def _vet_one(dtP: pd.DataFrame) -> dict:
    """Compute per-securitization error counts. dtP must be a single-sec slice, sorted."""
    sens = const.minSens()
    out = {c: 0 for c in const.rawCols()}

    out['Count'] = int(dtP['assetNumber'].nunique()) if 'assetNumber' in dtP.columns else 0

    if all(c in dtP.columns for c in
           ('reportingPeriodBeginningLoanBalanceAmount', 'reportingPeriodBeginningDate')):
        beg_dates = dtP['reportingPeriodBeginningDate']
        min_beg = beg_dates.min()
        out['OpenBal'] = float(
            dtP.loc[beg_dates == min_beg, 'reportingPeriodBeginningLoanBalanceAmount'].sum()
        )
        out['StartMonth'] = min_beg
        out['EndMonth'] = beg_dates.max()

    if 'reportingPeriodEndingDate' in dtP.columns:
        end_dates = dtP['reportingPeriodEndingDate']
        expected = pd.date_range(start=end_dates.min(), end=end_dates.max(), freq='M')
        present_months = set(end_dates.dropna().unique())
        out['MissingMonths'] = int(sum(1 for d in expected if d not in present_months))

    # Balance walks and increases: require sorted slice (caller sorted by asset+date).
    if all(c in dtP.columns for c in
           ('reportingPeriodBeginningLoanBalanceAmount', 'reportingPeriodActualEndBalanceAmount',
            'assetNumber', 'securitizationKey')):
        beg = dtP['reportingPeriodBeginningLoanBalanceAmount'].to_numpy()
        end = dtP['reportingPeriodActualEndBalanceAmount'].to_numpy()
        same_asset = dtP['assetNumber'].to_numpy()[1:] == dtP['assetNumber'].to_numpy()[:-1]
        same_sec = dtP['securitizationKey'].to_numpy()[1:] == dtP['securitizationKey'].to_numpy()[:-1]
        contiguous = same_asset & same_sec
        out['Walk'] = int(((beg[1:] != end[:-1]) & contiguous).sum())
        out['IncrBal'] = int(((beg[:-1] < beg[1:]) & contiguous).sum())

    if all(c in dtP.columns for c in
           ('reportingPeriodBeginningLoanBalanceAmount', 'reportingPeriodActualEndBalanceAmount',
            'actualPrincipalCollectedAmount', 'chargedoffPrincipalAmount',
            'otherPrincipalAdjustmentAmount')):
        diff = (
            (dtP['reportingPeriodBeginningLoanBalanceAmount']
             - dtP['reportingPeriodActualEndBalanceAmount'])
            - (dtP['actualPrincipalCollectedAmount']
               + dtP['chargedoffPrincipalAmount']
               + dtP['otherPrincipalAdjustmentAmount'])
        )
        out['Pmts'] = int((diff.abs() > sens).sum())

    # Month-over-month missing/extra assets
    if all(c in dtP.columns for c in
           ('reportingPeriodBeginningDate', 'reportingPeriodActualEndBalanceAmount',
            'assetNumber')):
        secMonths = np.sort(dtP['reportingPeriodBeginningDate'].dropna().unique())
        missing = extra = 0
        for i in range(len(secMonths) - 1):
            this_month = dtP['reportingPeriodBeginningDate'] == secMonths[i]
            next_month = dtP['reportingPeriodBeginningDate'] == secMonths[i + 1]
            this_loans = set(dtP.loc[
                this_month & (dtP['reportingPeriodActualEndBalanceAmount'].abs() > sens),
                'assetNumber',
            ].unique())
            next_loans = set(dtP.loc[next_month, 'assetNumber'].unique())
            missing += len(this_loans - next_loans)
            extra += len(next_loans - this_loans)
        out['Missing'] = missing
        out['Extra'] = extra

    # COExtra: count of rows that exist for an asset *after* it was charged off.
    # Previously O(N^2) — now a groupby + merge.
    if all(c in dtP.columns for c in
           ('assetNumber', 'reportingPeriodBeginningDate', 'chargedoffPrincipalAmount')):
        co_rows = dtP.loc[dtP['chargedoffPrincipalAmount'] > sens,
                          ['assetNumber', 'reportingPeriodBeginningDate']]
        if not co_rows.empty:
            co_first = (
                co_rows.groupby('assetNumber')['reportingPeriodBeginningDate']
                       .min()
                       .rename('_co_date')
            )
            joined = (
                dtP[['assetNumber', 'reportingPeriodBeginningDate']]
                .merge(co_first, left_on='assetNumber', right_index=True, how='inner')
            )
            out['COExtra'] = int(
                (joined['reportingPeriodBeginningDate'] > joined['_co_date']).sum()
            )

    # Duplicates on (asset, beg date, sec key)
    dup_keys = _present(
        ['assetNumber', 'reportingPeriodBeginningDate', 'securitizationKey'], dtP
    )
    if dup_keys:
        out['Dupes'] = int(dtP.duplicated(subset=dup_keys, keep=False).sum())

    if 'reportingPeriodBeginningLoanBalanceAmount' in dtP.columns:
        out['NegOpenBal'] = int((dtP['reportingPeriodBeginningLoanBalanceAmount'] < 0).sum())
    if 'reportingPeriodActualEndBalanceAmount' in dtP.columns:
        out['NegCloseBal'] = int((dtP['reportingPeriodActualEndBalanceAmount'] < 0).sum())

    rate_cols = _present(const.rateFields(), dtP)
    if rate_cols:
        out['RateNeg'] = int((dtP[rate_cols] < 0).sum().sum())
        out['RatePos'] = int((dtP[rate_cols] > 1).sum().sum())

    int_cols = _present(const.integerFields(), dtP)
    if int_cols:
        out['Integer'] = int(
            (dtP[int_cols].mod(1, axis=0, fill_value=0) != 0).sum().sum()
        )

    if 'chargedoffPrincipalAmount' in dtP.columns:
        co = dtP['chargedoffPrincipalAmount']
        out['NegCO'] = int((co < 0).sum())
        if 'reportingPeriodBeginningLoanBalanceAmount' in dtP.columns:
            beg = dtP['reportingPeriodBeginningLoanBalanceAmount']
            mask_nz = co.abs() > sens
            out['PartialCO'] = int(((co < beg) & mask_nz).sum())
            out['GreaterCO'] = int(((co > beg) & mask_nz).sum())

    if 'repossessedProceedsAmount' in dtP.columns:
        out['NegRepo'] = int((dtP['repossessedProceedsAmount'] < 0).sum())
    if 'recoveredAmount' in dtP.columns:
        out['NegRecov'] = int((dtP['recoveredAmount'] < 0).sum())

    return out


def data_vetting(dtPmts: pd.DataFrame):
    """Per-securitization error/description rollup.

    Returns ``(dtErrors, dtDescNum, dtDescStr)``. ``dtErrors`` is rows-by-metric,
    columns-by-securitization. The raw frame is sorted before slicing so the
    walk-error check actually means something.
    """
    if 'securitizationKey' not in dtPmts.columns:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    sort_cols = _present(['securitizationKey', 'assetNumber', 'reportingPeriodBeginningDate'], dtPmts)
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

    # Build dtErrors from records dict to avoid per-cell .ix assignment.
    dtErrors = pd.DataFrame.from_dict(error_records, orient='columns')
    dtErrors = dtErrors.reindex(const.rawCols())
    if cf_frames:
        dtCF = pd.concat(cf_frames, axis=1)
        dtCF = dtCF.reindex(columns=sec_keys)
        dtErrors = pd.concat([dtErrors, dtCF], axis=0)

    dtDescNum = pd.concat(desc_num_frames, axis=1) if desc_num_frames else pd.DataFrame()
    dtDescStr = pd.concat(desc_str_frames, axis=1) if desc_str_frames else pd.DataFrame()
    return dtErrors, dtDescNum, dtDescStr
