"""Net-yield series for the methods comparison page — reads the persisted
abs.loan_month_agg table (built by build_loan_month_agg.py), so it's instant and
never re-scans the ~33M loan_months rows.

Per index-universe month, annualized over average balance:
  gross (cash)    = 12 * cash_interest / avg_bal          -- actual interest collected
  gross (accrued) = accrued_interest_annl / avg_bal        -- coupon * balance (would-be)
  net loss        = 12 * (chargeoff - recovery) / avg_bal
  net yield       = gross - net loss   (both bases)

Writes web/data/netyield.json with both bases so the chart can compare them.

    python web/build_netyield.py
"""
from __future__ import annotations

import json
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


def main() -> int:
    with psycopg.connect(_dsn()) as c, c.cursor() as cur:
        cur.execute(
            "select report_month, n_trusts, cash_interest, accrued_interest_annl, "
            "chargeoff, recovery, avg_bal from abs.loan_month_agg order by report_month"
        )
        rows = cur.fetchall()

    series = []
    for month, n_trusts, cash_int, accr_int, chargeoff, recovery, avg_bal in rows:
        if not avg_bal:
            continue
        avg_bal = float(avg_bal)
        gross_cash = 12.0 * float(cash_int or 0) / avg_bal
        gross_accr = float(accr_int or 0) / avg_bal
        net_loss = 12.0 * (float(chargeoff or 0) - float(recovery or 0)) / avg_bal
        series.append({
            "date": month.strftime("%Y-%m-%d"),
            "n_trusts": int(n_trusts),
            "gross_cash": round(gross_cash * 100, 3),
            "gross_accrued": round(gross_accr * 100, 3),
            "net_loss": round(net_loss * 100, 3),
            "net_yield": round((gross_cash - net_loss) * 100, 3),          # cash (primary)
            "net_yield_accrued": round((gross_accr - net_loss) * 100, 3),
        })

    out = {
        "source": "abs.loan_month_agg (persisted marks)",
        "n_months": len(series),
        "series": series,
    }
    (ROOT / "web" / "data" / "netyield.json").write_text(json.dumps(out))
    cov = f"{series[0]['date']}..{series[-1]['date']}" if series else "none"
    last = series[-1] if series else {}
    print(f"netyield.json: {len(series)} months ({cov}); "
          f"latest cash net yield {last.get('net_yield')}%, accrued {last.get('net_yield_accrued')}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
