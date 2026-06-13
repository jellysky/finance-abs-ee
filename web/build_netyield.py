"""Accrued net-yield series for the methods comparison page.

Pools the index-universe (`in_index`) loan-months in Supabase by report month:

  gross yield (annl) = sum(int_rate * beg_balance) / sum(avg_balance)   ~ WA coupon
  net loss   (annl)  = 12 * sum(chargeoff - recovery) / sum(avg_balance)
  net yield  (annl)  = gross yield - net loss

ACCRUED basis: interest is coupon * balance (what the pool *would* earn), not
cash collected — so it reads a touch rosy in stress. This is the quick preview;
the cash-interest version uses int_collected once the re-load populates it.

Re-runnable: as `load_loans.py` brings more trusts online, just run this again
to widen/refresh the series. Writes web/data/netyield.json.

    ./.venv/bin/python web/build_netyield.py
"""
from __future__ import annotations

import json
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


SQL = """
select report_month,
       count(distinct trust_id)                            as n_trusts,
       sum(int_rate * beg_balance)                         as gross_num,
       sum((coalesce(beg_balance,end_balance)
            + coalesce(end_balance,beg_balance)) / 2.0)    as avg_bal,
       sum(coalesce(chargeoff,0) - coalesce(recovery,0))   as net_loss_dollars
from abs.loan_months
where in_index and beg_balance is not null
group by report_month
order by report_month
"""


def main() -> int:
    with psycopg.connect(_dsn()) as c, c.cursor() as cur:
        cur.execute("select count(distinct trust_id) from abs.loan_months where in_index")
        n_loaded = cur.fetchone()[0]
        cur.execute(SQL)
        rows = cur.fetchall()

    series = []
    for month, n_trusts, gross_num, avg_bal, net_loss in rows:
        if not avg_bal:
            continue
        gross = float(gross_num or 0) / float(avg_bal)
        loss = 12.0 * float(net_loss or 0) / float(avg_bal)
        series.append({
            "date": month.strftime("%Y-%m-%d"),
            "gross_yield": round(gross * 100, 3),
            "net_loss": round(loss * 100, 3),
            "net_yield": round((gross - loss) * 100, 3),
            "n_trusts": int(n_trusts),
        })

    out = {
        "basis": "accrued (coupon x balance)",
        "trusts_loaded": int(n_loaded),
        "trusts_total": 18,
        "n_months": len(series),
        "series": series,
    }
    (ROOT / "web" / "data" / "netyield.json").write_text(json.dumps(out))
    cov = f"{series[0]['date']}..{series[-1]['date']}" if series else "none"
    print(f"netyield.json: {len(series)} months ({cov}), "
          f"{n_loaded}/18 trusts loaded so far")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
