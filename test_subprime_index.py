"""Tests for the subprime auto credit index (universe / metrics / index).

Two layers of checking:

  * Hand-computable synthetic cases verify the exact metric arithmetic
    (delinquency shares, Current->30 roll rate, net loss, recovery rate) and
    the universe qualification rule.
  * An end-to-end run against whatever auto-loan XML is on disk confirms the
    pipeline composes with the real parser output (the on-disk Ally 2017 deal
    is prime, so it should be classified non-qualifying, not crash).

Run: ``python test_subprime_index.py`` (plain asserts, no pytest needed) or
``pytest test_subprime_index.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

import index as idx_mod
import metrics as metrics_mod
import universe as universe_mod

ROOT = Path(__file__).resolve().parent
APPROX = 1e-9


def _month(y: int, m: int) -> pd.Timestamp:
    return pd.Timestamp(year=y, month=m, day=1)


# ---------------------------------------------------------------------------
# Synthetic builders
# ---------------------------------------------------------------------------

def _rows(records: list[dict]) -> pd.DataFrame:
    """Build an enriched-shape frame from compact per-loan-month dicts.

    Fills the columns the metrics layer reads, defaulting the loss/recovery
    fields to 0 and deriving end-of-month reporting dates.
    """
    df = pd.DataFrame(records)
    df["reportingPeriodBeginningDate"] = pd.to_datetime(df["month"])
    df["reportingPeriodEndingDate"] = (
        df["reportingPeriodBeginningDate"] + pd.offsets.MonthEnd(0)
    )
    for col in ("chargedoffPrincipalAmount", "recoveredAmount", "netLosses"):
        if col not in df.columns:
            df[col] = 0.0
    # Default any unset beginning balance to that row's end balance (per-row, so
    # one row setting it explicitly doesn't leave the rest NaN).
    beg = "reportingPeriodBeginningLoanBalanceAmount"
    if beg not in df.columns:
        df[beg] = df["reportingPeriodActualEndBalanceAmount"]
    else:
        df[beg] = df[beg].fillna(df["reportingPeriodActualEndBalanceAmount"])
    return df.drop(columns=["month"])


def _three_loan_trust() -> pd.DataFrame:
    """A single trust, 3 loans x 3 months, designed for exact metric checks."""
    rec = [
        # 2023-01 — one loan 30 DPD
        dict(securitizationKey="TEST", assetNumber="A", month="2023-01-01",
             monthsDelinquent=0, reportingPeriodActualEndBalanceAmount=100),
        dict(securitizationKey="TEST", assetNumber="B", month="2023-01-01",
             monthsDelinquent=0, reportingPeriodActualEndBalanceAmount=100),
        dict(securitizationKey="TEST", assetNumber="C", month="2023-01-01",
             monthsDelinquent=1, reportingPeriodActualEndBalanceAmount=100),
        # 2023-02 — A rolls current->30, C worsens to 60
        dict(securitizationKey="TEST", assetNumber="A", month="2023-02-01",
             monthsDelinquent=1, reportingPeriodActualEndBalanceAmount=100),
        dict(securitizationKey="TEST", assetNumber="B", month="2023-02-01",
             monthsDelinquent=0, reportingPeriodActualEndBalanceAmount=100),
        dict(securitizationKey="TEST", assetNumber="C", month="2023-02-01",
             monthsDelinquent=2, reportingPeriodActualEndBalanceAmount=100),
        # 2023-03 — A charges off (recover 40), B current, C 90 DPD
        dict(securitizationKey="TEST", assetNumber="A", month="2023-03-01",
             monthsDelinquent=5, reportingPeriodActualEndBalanceAmount=0,
             chargedoffPrincipalAmount=100, recoveredAmount=40, netLosses=60,
             reportingPeriodBeginningLoanBalanceAmount=100),
        dict(securitizationKey="TEST", assetNumber="B", month="2023-03-01",
             monthsDelinquent=0, reportingPeriodActualEndBalanceAmount=100),
        dict(securitizationKey="TEST", assetNumber="C", month="2023-03-01",
             monthsDelinquent=3, reportingPeriodActualEndBalanceAmount=100),
    ]
    return _rows(rec)


def _qualification_frame() -> pd.DataFrame:
    """Two subprime trusts + one prime trust, sized for the universe rule."""
    rng = np.random.default_rng(531)
    frames = []
    specs = [
        ("Subprime Auto Trust 2022-1", 600, 24, True),
        ("Subprime Auto Trust 2022-2", 615, 24, True),
        ("Prime Auto Trust 2022-1", 730, 24, False),
        ("Tiny Subprime Trust 2022-1", 590, 4, False),  # subprime but too small
    ]
    for secname, fico, n_loans, _big in specs:
        loan_bal = 20_000_000.0  # so n>=12 loans clears the $200M pool floor
        for i in range(n_loans):
            for m in range(30):
                month = (_month(2022, 1) + pd.offsets.MonthBegin(m))
                # mild seasoning: delinquency creeps up with age
                md = 0
                if rng.random() < 0.02 * (1 + m / 30):
                    md = int(rng.integers(1, 5))
                bal = loan_bal * max(0.0, 1 - m / 60)
                frames.append(dict(
                    securitizationKey=secname, assetNumber=f"{secname[:4]}{i}",
                    month=str(month.date()), monthsFromCutoffDate=float(m),
                    monthsDelinquent=md,
                    reportingPeriodActualEndBalanceAmount=bal,
                    reportingPeriodBeginningLoanBalanceAmount=bal,
                    consumerCreditScore=float(fico + rng.integers(-15, 15)),
                    originalLoanAmount=loan_bal,
                    beginningBalanceAtCutoffDate=loan_bal,
                ))
    return _rows(frames)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_metric_arithmetic() -> None:
    tm = metrics_mod.trust_month_metrics(_three_loan_trust())
    jan, feb, mar = (("TEST", _month(2023, m)) for m in (1, 2, 3))

    # Delinquency shares (balance-weighted, active denominator).
    assert abs(tm.loc[jan, "delq30plus"] - 1 / 3) < APPROX
    assert abs(tm.loc[feb, "delq30plus"] - 2 / 3) < APPROX
    assert abs(tm.loc[feb, "delq60plus"] - 1 / 3) < APPROX
    # March: A charged off -> excluded from active denom (B + C = 200); C is 90 DPD.
    assert abs(tm.loc[mar, "delq30plus"] - 1 / 2) < APPROX

    # Roll Current->30: Jan has A,B current (200); A rolls to 30 in Feb -> 0.5.
    assert abs(tm.loc[jan, "roll_c_to_30"] - 0.5) < APPROX
    # Feb: only B current (100); stays current in Mar -> 0.0.
    assert abs(tm.loc[feb, "roll_c_to_30"] - 0.0) < APPROX

    # March loss metrics: net loss 60 on beg pool 300 (100+100+100), annualized.
    assert abs(tm.loc[mar, "net_loss_annl"] - 12 * 60 / 300) < APPROX
    assert abs(tm.loc[mar, "recovery_rate"] - 40 / 100) < APPROX
    print("  test_metric_arithmetic: OK")


def test_pooling_matches_loan_level() -> None:
    # Two identical trusts -> pooled ratio equals the single-trust ratio.
    one = _three_loan_trust()
    two = pd.concat([one, one.assign(securitizationKey="TEST2")], ignore_index=True)
    tm = metrics_mod.trust_month_metrics(two)
    pooled = idx_mod.pool_metrics(tm)
    jan = _month(2023, 1)
    assert int(pooled.loc[jan, "n_trusts"]) == 2
    assert abs(pooled.loc[jan, "delq30plus"] - 1 / 3) < APPROX
    assert abs(pooled.loc[jan, "roll_c_to_30"] - 0.5) < APPROX
    print("  test_pooling_matches_loan_level: OK")


def test_universe_rule() -> None:
    df = _qualification_frame()
    uni = universe_mod.build_universe(df)
    assert uni.loc["Subprime Auto Trust 2022-1", "qualifies"]
    assert uni.loc["Subprime Auto Trust 2022-2", "qualifies"]
    assert not uni.loc["Prime Auto Trust 2022-1", "qualifies"]     # FICO too high
    assert not uni.loc["Tiny Subprime Trust 2022-1", "qualifies"]  # pool too small
    assert uni.loc["Prime Auto Trust 2022-1", "wavg_fico"] >= 640
    print("  test_universe_rule: OK")


def test_build_index_end_to_end_synthetic() -> None:
    df = _qualification_frame()
    idx, uni, tm = idx_mod.build_index(df)
    assert int(uni["qualifies"].sum()) == 2
    assert not idx.empty
    assert "stress_index" in idx.columns
    assert "covid_flag" in idx.columns
    # The synthetic sample is 2022-2024, so no COVID months -> all flags False.
    assert not idx["covid_flag"].any()
    # Constituent count never exceeds the 2 qualifying trusts.
    assert idx["n_trusts"].max() <= 2
    # Once enough baseline accrues, the stress index is finite.
    assert idx["stress_index"].notna().any()
    print("  test_build_index_end_to_end_synthetic: OK")


def test_covid_flag() -> None:
    # Build a frame spanning the COVID window and confirm flagging.
    rec = []
    for m in range(40):  # 2019-06 .. ~2022-09
        month = _month(2019, 6) + pd.offsets.MonthBegin(m)
        rec.append(dict(securitizationKey="X", assetNumber="A",
                        month=str(month.date()), monthsDelinquent=0,
                        reportingPeriodActualEndBalanceAmount=100,
                        reportingPeriodBeginningLoanBalanceAmount=100))
    tm = metrics_mod.trust_month_metrics(_rows(rec))
    pooled = idx_mod.pool_metrics(tm)
    out = idx_mod.build_stress_index(pooled)
    flagged = out.index[out["covid_flag"]]
    assert flagged.min() == idx_mod.COVID_START
    assert flagged.max() == idx_mod.COVID_END
    assert len(flagged) == 9  # Apr 2020 .. Dec 2020 inclusive
    print("  test_covid_flag: OK")


def test_real_xml_pipeline() -> None:
    """Smoke: run the real parser + universe on whatever loan XML is on disk."""
    import autoLoanParser
    import utility

    loan_dir = ROOT / "Auto Loans"
    xmls = sorted(loan_dir.glob("*.xml")) if loan_dir.exists() else []
    if not xmls:
        print("  test_real_xml_pipeline: SKIPPED (no XML on disk)")
        return

    df = utility.read_ald_xml(xmls[0])
    df["securitizationKey"] = "Ally Auto Receivables Trust 2017-1"
    df["shelf"] = "Ally Auto Receivables Trust"
    df["reportDate"] = "20170525"
    enriched = autoLoanParser.append_calc_fields(autoLoanParser.clean_ald_files(df))

    uni = universe_mod.build_universe(enriched)
    assert len(uni) == 1
    # Ally 2017-1 is a prime shelf -> must not qualify as subprime.
    assert not uni["qualifies"].iloc[0], (
        f"Ally classified subprime? fico={uni['wavg_fico'].iloc[0]:.0f}"
    )
    print(f"  test_real_xml_pipeline: OK (Ally wavg_fico="
          f"{uni['wavg_fico'].iloc[0]:.0f}, non-qualifying as expected)")


def main() -> int:
    tests = [
        test_metric_arithmetic,
        test_pooling_matches_loan_level,
        test_universe_rule,
        test_build_index_end_to_end_synthetic,
        test_covid_flag,
        test_real_xml_pipeline,
    ]
    failed = 0
    for t in tests:
        print(f"=== {t.__name__} ===")
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAILED: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc(limit=3)
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
