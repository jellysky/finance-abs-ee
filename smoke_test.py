"""Smoke test for the ABS-EE downloader and both cleaner parsers.

Runs the full parser path (read XML -> clean_ald_files -> append_calc_fields
-> data_vetting) against on-disk XMLs. Also exercises main.create_comparison
on the cleaned/enriched output to confirm the reporting layer works.

By default, no network access — XMLs already on disk under Auto Loans/ and
Auto Leases/ are used. If a class has no XML on disk, that class is skipped
with a clear message. Pass --download to auto-fetch sample XMLs.

Usage:
    python smoke_test.py                  # quiet, on-disk XMLs only
    python smoke_test.py -v               # show per-step INFO log lines
    python smoke_test.py --download       # fetch the Ford lease XML (~88 MB) if missing
    python smoke_test.py -v --download
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from pathlib import Path

import pandas as pd

import autoLeaseParser
import autoLoanParser
import main as reporting
import utility


ROOT = Path(__file__).resolve().parent
LOAN_DIR = ROOT / "Auto Loans"
LEASE_DIR = ROOT / "Auto Leases"

LEASE_SAMPLE = {
    "secname": "Ford Credit Auto Lease Trust 2024-A",
    "filename": "Ford_Credit_Auto_Lease_Trust_2024-A_20240110_0001519881-24-000007.xml",
    "entitytype": "Trust",
    "assetclass": "Auto Leases",
    "reportdate": "20240110",
    "url": "https://www.sec.gov/Archives/edgar/data/1519881/000151988124000007/autoleaseinitial1148lp.xml",
}


def step(label: str) -> None:
    print(f"\n=== {label} ===")


def safe_call(name: str, fn, *args):
    try:
        out = fn(*args)
        print(f"  {name}: OK")
        return out, None
    except Exception as e:
        print(f"  {name}: FAILED ({type(e).__name__}: {e})")
        traceback.print_exc(limit=4)
        return None, e


def find_xml(directory: Path) -> Path | None:
    if not directory.exists():
        return None
    candidates = sorted(directory.glob("*.xml"), key=lambda p: p.stat().st_size)
    return candidates[0] if candidates else None


def maybe_download_lease(allow_download: bool) -> Path | None:
    existing = find_xml(LEASE_DIR)
    if existing is not None:
        return existing
    if not allow_download:
        print(
            f"  No XML in {LEASE_DIR}/. Pass --download to fetch "
            f"{LEASE_SAMPLE['secname']} ({LEASE_SAMPLE['url']!s})."
        )
        return None
    print(f"  Downloading sample lease XML: {LEASE_SAMPLE['secname']} ...")
    df = pd.DataFrame([LEASE_SAMPLE])
    utility.download_filings(df, entity_types=["Trust"], asset_classes=["Auto Leases"])
    return find_xml(LEASE_DIR)


def run_parser(label: str, xml_path: Path, parser, secname: str, report_date: str) -> pd.DataFrame | None:
    step(f"{label}: parsing XML {xml_path.name}")
    df = utility.read_ald_xml(xml_path)
    df["securitizationKey"] = secname
    df["reportDate"] = report_date
    print(f"  rows: {len(df):,}  cols: {df.shape[1]}")

    step(f"{label}: clean_ald_files")
    cleaned, _ = safe_call("clean_ald_files", parser.clean_ald_files, df)
    if cleaned is None:
        return None
    print(f"  -> {len(cleaned):,} rows, {cleaned.shape[1]} cols")

    step(f"{label}: append_calc_fields")
    enriched, _ = safe_call("append_calc_fields", parser.append_calc_fields, cleaned)
    if enriched is None:
        return None
    print(f"  -> {len(enriched):,} rows, {enriched.shape[1]} cols")

    derived = [
        "age", "vintage", "loanToValueRatio", "monthsDelinquent",
        "principalPrepaid", "consumerCreditScore", "commercialCreditScore",
        "primeIndicator", "netLosses", "region", "summaryDate",
        "monthsFromCutoffDate", "ageFromCutoffDate", "beginningBalanceAtCutoffDate",
    ]
    present = [c for c in derived if c in enriched.columns]
    print(f"  derived fields produced: {len(present)}/{len(derived)}")
    missing = [c for c in derived if c not in present]
    if missing:
        print(f"  missing: {missing}")

    step(f"{label}: data_vetting")
    safe_call("data_vetting", parser.data_vetting, enriched)

    return enriched


def run_reporting(label: str, enriched: pd.DataFrame) -> None:
    step(f"{label}: main.create_comparison (reporting layer)")
    comp, _ = safe_call("create_comparison", reporting.create_comparison, enriched)
    if comp is not None:
        print(f"  -> {len(comp)} row(s); columns sample: {list(comp.columns[:5])} ...")

    step(f"{label}: main.create_rollrates_matrix")
    rr, _ = safe_call("create_rollrates_matrix", reporting.create_rollrates_matrix, enriched)
    if rr is not None:
        print(f"  -> {rr.shape[0]}x{rr.shape[1]} transition matrix")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="-v shows INFO log lines (per-field cleaning, etc.); "
                             "-vv shows DEBUG.")
    parser.add_argument("--download", action="store_true",
                        help="Fetch sample XML(s) from EDGAR if absent on disk.")
    parser.add_argument("--skip-lease", action="store_true",
                        help="Skip the auto-lease parser test.")
    args = parser.parse_args(argv)

    levels = [logging.WARNING, logging.INFO, logging.DEBUG]
    logging.basicConfig(
        level=levels[min(args.verbose, len(levels) - 1)],
        format="%(asctime)s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    os.chdir(ROOT)

    loan_xml = find_xml(LOAN_DIR)
    if loan_xml is None:
        print(f"No auto-loan XML in {LOAN_DIR}/. "
              f"Run `python utility.py download` to fetch one first.")
        return 1
    enriched_loan = run_parser(
        "AUTO LOAN", loan_xml, autoLoanParser,
        "Ally Auto Receivables Trust 2017-1", "20170525",
    )
    if enriched_loan is not None:
        run_reporting("AUTO LOAN", enriched_loan)

    if args.skip_lease:
        return 0
    lease_xml = maybe_download_lease(args.download)
    if lease_xml is None:
        print("\nSkipping lease parser test.")
        return 0
    enriched_lease = run_parser(
        "AUTO LEASE", lease_xml, autoLeaseParser,
        "Ford Credit Auto Lease Trust 2024-A", "20240110",
    )
    if enriched_lease is not None:
        # Reporting layer expects loan-shaped column names; remap.
        run_reporting("AUTO LEASE", autoLeaseParser.fit_reporting_model(enriched_lease))
    return 0


if __name__ == "__main__":
    sys.exit(main())
