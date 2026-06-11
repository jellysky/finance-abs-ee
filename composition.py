"""Exact per-month pool composition + duration for the subprime auto index.

Reads the loan-level table (abs.loan_months, in_index outstanding loans) and writes
csv/composition_by_month.csv with, per month:
  n_borrowers, pool_balance, wa_fico, wa_orig_term, wa_rem_term,
  sched_wal_months   (scheduled amortization WAL, no prepay)
  realized_wal_months(projected at the trailing-6mo observed run-off speed)

These feed the site's composition panel (web/build_site_data.py).
Run: python composition.py
"""
from __future__ import annotations

import csv
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent


def _dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); env[k] = v.strip().strip('"').strip("'")
    return env["DATABASE_URL"]


@lru_cache(maxsize=None)
def _wal_sched(N: int, r: float) -> float:
    """Scheduled WAL (months) of a level-amortizing loan: term N, annual rate r."""
    i = r / 12.0
    if N <= 0:
        return 0.0
    if i <= 0:
        return (N + 1) / 2.0
    pmt = i / (1 - (1 + i) ** (-N))
    bal, acc = 1.0, 0.0
    for t in range(1, N + 1):
        prin = min(max(pmt - bal * i, 0.0), bal)
        bal -= prin; acc += t * prin
    return acc


def main() -> int:
    conn = psycopg.connect(_dsn())
    cur = conn.cursor()

    # 1) exact per-month aggregates
    cur.execute("""
        select report_month, count(*) n, sum(end_balance) bal,
          sum(fico*end_balance) filter (where fico between 300 and 850)
            / nullif(sum(end_balance) filter (where fico between 300 and 850),0) wa_fico,
          sum(orig_term*end_balance) filter (where orig_term between 1 and 120)
            / nullif(sum(end_balance) filter (where orig_term between 1 and 120),0) wa_orig,
          sum(rem_term*end_balance) filter (where rem_term between 1 and 90)
            / nullif(sum(end_balance) filter (where rem_term between 1 and 90),0) wa_rem
        from abs.loan_months where in_index and end_balance > 0
        group by 1 order by 1""")
    base = {r[0]: {"n": int(r[1]), "bal": float(r[2]),
                   "fico": float(r[3]) if r[3] else None,
                   "orig": float(r[4]) if r[4] else None,
                   "rem": float(r[5]) if r[5] else None} for r in cur.fetchall()}

    # 2) scheduled WAL: weight per (month, rem_term, rate bucket)
    cur.execute("""
        select report_month, rem_term, round((int_rate*200)::numeric)/200.0 rate, sum(end_balance) bal
        from abs.loan_months
        where in_index and end_balance>0 and rem_term between 1 and 90 and int_rate is not null and int_rate>=0
        group by 1,2,3""")
    sched = defaultdict(lambda: [0.0, 0.0])
    for mo, N, rate, bal in cur.fetchall():
        sched[mo][0] += _wal_sched(int(N), float(rate)) * float(bal); sched[mo][1] += float(bal)

    # 3) realized WAL: per-deal trailing-6mo run-off, projected, balance-weighted
    cur.execute("""
        select trust_id, report_month, sum(beg_balance) beg, sum(end_balance) endb,
          sum(rem_term::numeric*end_balance) filter (where rem_term between 1 and 90)
            / nullif(sum(end_balance) filter (where rem_term between 1 and 90),0) wa_rem
        from abs.loan_months where in_index and end_balance>=0 and beg_balance>0
        group by 1,2 order by 1,2""")
    bydeal = defaultdict(list)
    for tid, mo, beg, endb, wa in cur.fetchall():
        bydeal[tid].append([mo, float(beg), float(endb), float(wa) if wa is not None else None])
    realized = defaultdict(lambda: [0.0, 0.0])
    for rows in bydeal.values():
        smms = []
        for i, (mo, beg, endb, wa) in enumerate(rows):
            smms.append(1 - endb / beg if beg > 0 else 0.0)
            win = smms[max(0, i - 5):i + 1]
            eff = max(sum(win) / len(win), 0.003)
            wal = 1.0 / eff
            if wa:
                wal = min(wal, wa)
            realized[mo][0] += wal * endb; realized[mo][1] += endb
    conn.close()

    out = ROOT / "csv" / "composition_by_month.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["month", "n_borrowers", "pool_balance", "wa_fico", "wa_orig_term",
                    "wa_rem_term", "sched_wal_months", "realized_wal_months"])
        for mo in sorted(base):
            b = base[mo]
            sw = sched[mo][0] / sched[mo][1] if sched[mo][1] else None
            rw = realized[mo][0] / realized[mo][1] if realized[mo][1] else None
            w.writerow([mo, b["n"], round(b["bal"]),
                        round(b["fico"]) if b["fico"] else "",
                        round(b["orig"], 1) if b["orig"] else "",
                        round(b["rem"], 1) if b["rem"] else "",
                        round(sw, 1) if sw else "", round(rw, 1) if rw else ""])
    print(f"Wrote {out} ({len(base)} months). Latest:")
    mo = max(base); b = base[mo]
    print(f"  {mo:%Y-%m}: {b['n']:,} borrowers, WA FICO {round(b['fico'])}, "
          f"WA orig term {b['orig']:.1f}mo, WA rem {b['rem']:.1f}mo, "
          f"sched WAL {sched[mo][0]/sched[mo][1]:.1f}mo, realized WAL {realized[mo][0]/realized[mo][1]:.1f}mo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
