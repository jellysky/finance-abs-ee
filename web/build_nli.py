"""Serention Net-Loss Index (NLI) — the leading, gross loss rate used by the Net-Loss Future.

Loss is recognized when a loan ENTERS 90+ DPD (not when the charge-off is booked ~a year
later), scaled by the empirical roll-to-charge-off of ~0.81 (see csv/roll_matrix.csv):

    NLI_t (%/yr) = 100 * 12 * 0.81 * (balance entering 90+ DPD in month t) / (beginning pool balance)

Recoveries are excluded (they lag). Builds the persisted table abs.nli_monthly (one heavy
pass over loan_months), then writes web/data/nli.json + csv/nli_monthly.csv.

    python web/build_nli.py
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
ROLL = 0.81  # empirical 90+DPD -> charge-off roll (from the roll matrix)

for line in (ROOT / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); os.environ.setdefault(k, v.strip().strip('"').strip("'"))

CREATE = f"""
drop table if exists abs.nli_monthly;
create table abs.nli_monthly as
with seq as (
  select trust_id, asset_number, report_month,
         coalesce(dpd,0) as dpd, coalesce(beg_balance,0) as beg,
         lag(coalesce(dpd,0))  over w as pdpd,
         lag(report_month)     over w as pm
  from abs.loan_months
  where in_index
  window w as (partition by trust_id, asset_number order by report_month)
),
agg as (
  select report_month,
         sum(beg) as beg_pool,
         sum(case when dpd >= 90
                   and (pdpd is null or pdpd < 90 or pm <> report_month - interval '1 month')
                  then beg else 0 end) as new90_bal
  from seq
  group by report_month
)
select report_month, beg_pool, new90_bal,
       round((100.0 * 12 * {ROLL} * new90_bal / nullif(beg_pool,0))::numeric, 3) as nli_pct
from agg
order by report_month
"""


def main() -> int:
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c, c.cursor() as cur:
        cur.execute("set statement_timeout to '1200s'")  # one heavy LAG pass over ~33M rows
        for stmt in CREATE.strip().split(";\n"):
            if stmt.strip():
                cur.execute(stmt)
        cur.execute("select report_month, beg_pool, new90_bal, nli_pct from abs.nli_monthly order by report_month")
        rows = cur.fetchall()

    series = [{"date": m.strftime("%Y-%m-%d"),
               "nli": float(nli) if nli is not None else None,
               "new90_pct": round(100.0 * float(n90) / float(bp), 3) if bp else None}
              for m, bp, n90, nli in rows]
    (ROOT / "web" / "data" / "nli.json").write_text(json.dumps({
        "roll_factor": ROLL, "n_months": len(series),
        "latest": series[-1] if series else None, "series": series,
    }))
    with open(ROOT / "csv" / "nli_monthly.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["month", "beg_pool", "new90_bal", "nli_pct"])
        for m, bp, n90, nli in rows:
            w.writerow([m, bp, n90, nli])

    last = series[-1] if series else {}
    print(f"abs.nli_monthly: {len(series)} months; latest {last.get('date')} NLI = {last.get('nli')}%/yr "
          f"(new-90+ {last.get('new90_pct')}% of pool)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
