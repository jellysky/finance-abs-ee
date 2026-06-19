"""Empirical delinquency roll analysis on abs.loan_months (loan-level, all qualifying trusts).

(1) Eventual charge-off probability given a loan ever reached >= a DPD bucket.
(2) Monthly state-transition (roll) matrix: P(next-month bucket | this-month bucket).

Buckets by days past due (dpd): Cur (<30), 30 (30-59), 60 (60-89), 90 (90-119),
120+ (>=120); CO = charge-off event that month; PaidOff = loan exits w/o charge-off.
Recoveries are ignored (gross losses). Writes csv/roll_matrix.csv.
"""
import os, csv
from pathlib import Path
import psycopg

ROOT = Path(__file__).resolve().parent
for line in (ROOT / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); os.environ.setdefault(k, v.strip().strip('"').strip("'"))

Q_EVENTUAL = """
with pa as (
  select trust_id, asset_number,
         max(coalesce(dpd,0)) as maxdpd,
         max(case when coalesce(chargeoff,0) > 0 then 1 else 0 end) as charged
  from abs.loan_months
  group by trust_id, asset_number
)
select
  count(*) filter (where maxdpd>=30)  as ever30,  count(*) filter (where maxdpd>=30  and charged=1) as co30,
  count(*) filter (where maxdpd>=60)  as ever60,  count(*) filter (where maxdpd>=60  and charged=1) as co60,
  count(*) filter (where maxdpd>=90)  as ever90,  count(*) filter (where maxdpd>=90  and charged=1) as co90,
  count(*) filter (where maxdpd>=120) as ever120, count(*) filter (where maxdpd>=120 and charged=1) as co120
from pa
"""

Q_TRANS = """
with seq as (
  select trust_id, asset_number, report_month,
         coalesce(dpd,0) as dpd, coalesce(chargeoff,0) as co,
         lead(report_month) over w as nm,
         lead(coalesce(dpd,0)) over w as ndpd,
         lead(coalesce(chargeoff,0)) over w as nco
  from abs.loan_months
  window w as (partition by trust_id, asset_number order by report_month)
),
t as (
  select
    case when dpd>=120 then '120+' when dpd>=90 then '90' when dpd>=60 then '60'
         when dpd>=30 then '30' else 'Cur' end as fromb,
    case when nm is null or nm <> (report_month + interval '1 month')
              then (case when co>0 then 'CO' else 'PaidOff' end)
         when nco>0 then 'CO' when ndpd>=120 then '120+' when ndpd>=90 then '90'
         when ndpd>=60 then '60' when ndpd>=30 then '30' else 'Cur' end as tob
  from seq
  where co = 0   -- charge-off is absorbing; only roll FROM non-charged states
)
select fromb, tob, count(*) as c from t group by fromb, tob order by fromb, tob
"""

with psycopg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
    cur.execute("set statement_timeout to '1200s'")
    print("=== (1) Eventual charge-off | ever reached bucket ===", flush=True)
    cur.execute(Q_EVENTUAL)
    e30, c30, e60, c60, e90, c90, e120, c120 = cur.fetchone()
    for label, ever, co in [("30+ DPD", e30, c30), ("60+ DPD", e60, c60),
                            ("90+ DPD", e90, c90), ("120+ DPD", e120, c120)]:
        pct = 100.0 * co / ever if ever else 0
        print(f"  reached {label:8s}: {ever:>10,} loans -> {co:>10,} charged off  = {pct:5.1f}%", flush=True)

    print("\n=== (2) Monthly roll matrix: P(next | this) ===", flush=True)
    cur.execute(Q_TRANS)
    rows = cur.fetchall()

order = ["Cur", "30", "60", "90", "120+"]
tos = ["Cur", "30", "60", "90", "120+", "CO", "PaidOff"]
mat = {f: {t: 0 for t in tos} for f in order}
for fromb, tob, cnt in rows:
    if fromb in mat and tob in mat[fromb]:
        mat[fromb][tob] = cnt
print("  from \\ to   " + "".join(f"{t:>9}" for t in tos), flush=True)
for f in order:
    tot = sum(mat[f].values())
    if not tot: continue
    print(f"  {f:8s}   " + "".join(f"{100.0*mat[f][t]/tot:8.1f}%" for t in tos)
          + f"   (n={tot:,})", flush=True)

# persist
with open(ROOT / "csv" / "roll_matrix.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["from", "to", "count"])
    for fromb, tob, cnt in rows: w.writerow([fromb, tob, cnt])
print("\nwrote csv/roll_matrix.csv", flush=True)
