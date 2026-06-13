"""Materialize the per-month loan aggregate ONCE so downstream index queries are
instant — avoids re-scanning ~33M abs.loan_months rows every time. Creates the
113-row table abs.loan_month_agg with the dollar sums needed for both the cash
and accrued net-yield (and the loss leg).

Run after each load_loans.py reload:  python web/build_loan_month_agg.py
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent


def _dsn() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k == "DATABASE_URL":
                return v.strip().strip('"').strip("'")
    raise SystemExit("DATABASE_URL not found in .env")


CREATE = """
create table abs.loan_month_agg as
select report_month,
       count(distinct trust_id)                                       as n_trusts,
       sum(coalesce(int_collected, 0))                                as cash_interest,        -- monthly $
       sum(coalesce(int_rate, 0) * coalesce(beg_balance, 0))          as accrued_interest_annl, -- annual $
       sum(coalesce(chargeoff, 0))                                    as chargeoff,
       sum(coalesce(recovery, 0))                                     as recovery,
       sum((coalesce(beg_balance, end_balance)
            + coalesce(end_balance, beg_balance)) / 2.0)              as avg_bal,
       sum(coalesce(beg_balance, 0))                                  as beg_bal
from abs.loan_months
where in_index and beg_balance is not null
group by report_month
"""


def main() -> int:
    with psycopg.connect(_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute("set statement_timeout to '900s'")  # one-time full scan of ~33M rows
        cur.execute("drop table if exists abs.loan_month_agg")
        cur.execute(CREATE)
        cur.execute("alter table abs.loan_month_agg add primary key (report_month)")
        cur.execute("select count(*), min(report_month), max(report_month) from abs.loan_month_agg")
        n, lo, hi = cur.fetchone()
        print(f"abs.loan_month_agg built: {n} months ({lo} .. {hi})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
