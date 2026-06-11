"""Load lean loan-level data into Supabase (Option B), disk-safe + resumable.

For each qualifying subprime trust: download its monthly tape -> clean (dedup,
date parse, rate-normalize; NO append_calc_fields) -> select ~16 lean columns ->
COPY into abs.loan_months -> DELETE that deal's XMLs before the next deal.
Peak local disk stays ~one tape (~7 GB).

Resumable: a trust is skipped if it's already in abs.loan_load_log, so a pause /
crash / "I have to leave" just resumes on re-run with no re-download. To force a
full reload, TRUNCATE abs.loan_months and abs.loan_load_log first.

Usage:
    python load_loans.py --setup-only     # create tables + trusts dim, list targets
    python load_loans.py                   # full streaming load (run detached)
"""
from __future__ import annotations

import argparse
import io
import logging
import shutil
import sys
from pathlib import Path

import pandas as pd

import autoLoanParser
import run_index
import utility

log = logging.getLogger("absee.subprime.loans")
ROOT = run_index.ROOT
LOAN_DIR = run_index.LOAN_DIR

# Lean schema: raw fields that cover loan-level analysis (FICO/term/DPD/balances/
# losses/geo/vintage). Derived buckets are computed at query time.
NUM = ["originalLoanTerm", "remainingTermToMaturityNumber", "obligorCreditScore",
       "currentDelinquencyStatus", "reportingPeriodBeginningLoanBalanceAmount",
       "reportingPeriodActualEndBalanceAmount", "originalLoanAmount",
       "chargedoffPrincipalAmount", "recoveredAmount",
       "reportingPeriodInterestRatePercentage", "vehicleNewUsedCode"]
KEEP = set(NUM + ["assetNumber", "reportingPeriodBeginningDate",
                  "reportingPeriodEndingDate", "originationDate",
                  "obligorGeographicLocation"])

DDL = """
create schema if not exists abs;
create table if not exists abs.trusts (
  trust_id smallint primary key,
  securitization_key text unique not null,
  shelf text, wavg_fico numeric, qualifies boolean,
  first_month date, last_month date, exited date
);
create table if not exists abs.loan_months (
  trust_id smallint not null,
  asset_number text,
  report_month date,
  orig_date date,
  fico smallint,
  dpd integer,
  orig_term smallint,
  rem_term smallint,
  beg_balance real,
  end_balance real,
  orig_balance real,
  chargeoff real,
  recovery real,
  int_rate real,
  state text,
  new_used smallint,
  in_index boolean
);
create table if not exists abs.loan_load_log (
  trust_id smallint primary key,
  securitization_key text,
  n_rows bigint,
  loaded_at timestamptz default now()
);
"""

COPY_COLS = ("trust_id, asset_number, report_month, orig_date, fico, dpd, "
             "orig_term, rem_term, beg_balance, end_balance, orig_balance, "
             "chargeoff, recovery, int_rate, state, new_used, in_index")


def _dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v.strip().strip('"').strip("'")
    return env["DATABASE_URL"]


def _disk_free_gb() -> float:
    return shutil.disk_usage(ROOT).free / 1e9


def _datestr(s: pd.Series) -> pd.Series:
    dt = pd.to_datetime(s, errors="coerce")
    return dt.dt.strftime("%Y-%m-%d").where(dt.notna(), "")


def build_lean(cleaned: pd.DataFrame, trust_id: int, exited) -> pd.DataFrame:
    """Map a cleaned single-deal frame to the lean loan_months columns."""
    def col(name):
        return cleaned[name] if name in cleaned.columns else pd.Series(pd.NA, index=cleaned.index)

    beg = pd.to_datetime(col("reportingPeriodBeginningDate"), errors="coerce")
    month = beg.dt.to_period("M").dt.to_timestamp()
    out = pd.DataFrame({
        "trust_id": trust_id,
        "asset_number": col("assetNumber").astype("string"),
        "report_month": _datestr(month),
        "orig_date": _datestr(col("originationDate")),
        "fico": pd.to_numeric(col("obligorCreditScore"), errors="coerce").round().astype("Int64"),
        "dpd": pd.to_numeric(col("currentDelinquencyStatus"), errors="coerce").round().astype("Int64"),
        "orig_term": pd.to_numeric(col("originalLoanTerm"), errors="coerce").round().astype("Int64"),
        "rem_term": pd.to_numeric(col("remainingTermToMaturityNumber"), errors="coerce").round().astype("Int64"),
        "beg_balance": pd.to_numeric(col("reportingPeriodBeginningLoanBalanceAmount"), errors="coerce"),
        "end_balance": pd.to_numeric(col("reportingPeriodActualEndBalanceAmount"), errors="coerce"),
        "orig_balance": pd.to_numeric(col("originalLoanAmount"), errors="coerce"),
        "chargeoff": pd.to_numeric(col("chargedoffPrincipalAmount"), errors="coerce"),
        "recovery": pd.to_numeric(col("recoveredAmount"), errors="coerce"),
        "int_rate": pd.to_numeric(col("reportingPeriodInterestRatePercentage"), errors="coerce"),
        "state": col("obligorGeographicLocation").astype("string"),
        "new_used": pd.to_numeric(col("vehicleNewUsedCode"), errors="coerce").round().astype("Int64"),
    })
    # in_index = a month the index actually uses (qualifying trust, before exit).
    if pd.notna(exited):
        out["in_index"] = month < pd.Timestamp(exited)
    else:
        out["in_index"] = True
    return out


def copy_deal(conn, lean: pd.DataFrame) -> int:
    buf = io.StringIO()
    lean.to_csv(buf, index=False, header=False)
    buf.seek(0)
    with conn.cursor() as cur:
        with cur.copy(f"COPY abs.loan_months ({COPY_COLS}) FROM STDIN WITH (FORMAT csv, NULL '')") as cp:
            cp.write(buf.getvalue())
    return len(lean)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--listing", default="Inputs/dtABS.csv")
    p.add_argument("--setup-only", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")

    import psycopg

    listing = utility.read_listing(args.listing)
    sub = listing[(listing["entitytype"] == "Trust") & (listing["assetclass"] == "Auto Loans")]
    uni = pd.read_csv(ROOT / "csv" / "universe.csv",
                      parse_dates=["first_month", "last_month", "exited"])
    uni = uni.sort_values("securitization_key").reset_index(drop=True)
    uni["trust_id"] = uni.index + 1  # deterministic ids over all trusts
    tid = dict(zip(uni["securitization_key"], uni["trust_id"]))

    conn = psycopg.connect(_dsn())
    with conn.cursor() as cur:
        cur.execute(DDL)
        for _, r in uni.iterrows():
            cur.execute(
                "insert into abs.trusts (trust_id, securitization_key, shelf, wavg_fico,"
                " qualifies, first_month, last_month, exited) values (%s,%s,%s,%s,%s,%s,%s,%s)"
                " on conflict (trust_id) do update set qualifies=excluded.qualifies,"
                " exited=excluded.exited, wavg_fico=excluded.wavg_fico",
                (int(r["trust_id"]), r["securitization_key"], r.get("shelf"),
                 None if pd.isna(r["wavg_fico"]) else float(r["wavg_fico"]),
                 bool(r["qualifies"]),
                 None if pd.isna(r["first_month"]) else r["first_month"].date(),
                 None if pd.isna(r["last_month"]) else r["last_month"].date(),
                 None if pd.isna(r["exited"]) else r["exited"].date()))
        conn.commit()
        cur.execute("select trust_id from abs.loan_load_log")
        done = {row[0] for row in cur.fetchall()}

    targets = uni[uni["qualifies"]].copy()
    todo = targets[~targets["trust_id"].isin(done)]
    log.info("Qualifying trusts: %d | already loaded: %d | to load: %d",
             len(targets), len(targets) - len(todo), len(todo))
    for _, r in todo.iterrows():
        log.info("  todo: %s", r["securitization_key"])
    if args.setup_only:
        conn.close()
        return 0

    for _, r in todo.iterrows():
        secname = r["securitization_key"]
        deal_rows = sub[sub["secname"] == secname]
        log.info("=== %s (disk free %.0f GB) ===", secname, _disk_free_gb())
        utility.download_filings(deal_rows, ["Trust"], ["Auto Loans"])
        try:
            raw = utility.read_ald_files(deal_rows, "Trust", "Auto Loans",
                                         keep_cols=KEEP, numeric_cols=NUM)
            if raw.empty:
                log.warning("No data parsed for %s; skipping.", secname)
                continue
            cleaned = autoLoanParser.clean_ald_files(raw)
            lean = build_lean(cleaned, int(r["trust_id"]), r["exited"])
            n = copy_deal(conn, lean)
            with conn.cursor() as cur:
                cur.execute("insert into abs.loan_load_log (trust_id, securitization_key, n_rows)"
                            " values (%s,%s,%s) on conflict (trust_id) do update set"
                            " n_rows=excluded.n_rows, loaded_at=now()",
                            (int(r["trust_id"]), secname, n))
            conn.commit()
            log.info("Loaded %s: %d loan-months", secname, n)
        finally:
            removed = 0
            for fn in deal_rows["filename"].dropna().unique():
                f = LOAN_DIR / fn
                if f.exists():
                    f.unlink(); removed += 1
            log.info("Deleted %d XMLs (disk free now %.0f GB)", removed, _disk_free_gb())

    # Indexes (idempotent) once everything's loaded.
    with conn.cursor() as cur:
        cur.execute("select count(*) from abs.loan_load_log where trust_id = any(%s)",
                    ([int(x) for x in targets["trust_id"]],))
        n_loaded = cur.fetchone()[0]
        if n_loaded >= len(targets):
            log.info("All loaded; building indexes ...")
            cur.execute("create index if not exists loan_months_trust_month on abs.loan_months(trust_id, report_month)")
            cur.execute("create index if not exists loan_months_month on abs.loan_months(report_month)")
            cur.execute("create index if not exists loan_months_fico on abs.loan_months(fico)")
            conn.commit()
        cur.execute("select count(*) from abs.loan_months")
        total = cur.fetchone()[0]
    log.info("DONE. abs.loan_months total rows: %d. Disk free %.0f GB.", total, _disk_free_gb())
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
