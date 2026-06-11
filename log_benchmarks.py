"""Monthly logger for rating-agency auto-ABS benchmark figures.

KBRA's and Fitch's index series are paywalled, so there's no clean file to pull.
This maintains a small append-only store of the *public headline numbers* they
publish each month (in press releases / trade coverage), so a comparison series
to our index builds up over time. Append by hand when the monthly reports land.

Store: Inputs/benchmarks/agency_benchmarks.csv
  columns: date(YYYY-MM), series, metric, value, source, approx(0/1), note
  series : fitch_subprime | kbra_nonprime   (extend as needed)
  metric : net_loss_annl | delq60 | delq30 | recovery   (percentages)

Usage:
  python log_benchmarks.py                 # show current contents
  python log_benchmarks.py --add 2026-04 fitch_subprime delq60 6.30 "Fitch" \\
         --note "April reading" --approx 0
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "Inputs" / "benchmarks" / "agency_benchmarks.csv"
COLS = ["date", "series", "metric", "value", "source", "approx", "note"]
SERIES = {"fitch_subprime", "kbra_nonprime"}
METRICS = {"net_loss_annl", "delq60", "delq30", "recovery", "delq30plus"}


def _read() -> list[dict]:
    if not STORE.exists():
        return []
    with open(STORE) as f:
        return list(csv.DictReader(f))


def _write(rows: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda r: (r["series"], r["metric"], r["date"]))
    with open(STORE, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--add", nargs=5, metavar=("DATE", "SERIES", "METRIC", "VALUE", "SOURCE"),
                   help="Append one observation.")
    p.add_argument("--note", default="")
    p.add_argument("--approx", default="0", choices=["0", "1"])
    args = p.parse_args()
    rows = _read()

    if args.add:
        date, series, metric, value, source = args.add
        if series not in SERIES:
            print(f"warn: '{series}' not in known series {SERIES} (adding anyway)")
        if metric not in METRICS:
            print(f"warn: '{metric}' not in known metrics {METRICS} (adding anyway)")
        float(value)  # validate
        # replace any existing (date, series, metric) so re-logging corrects in place
        rows = [r for r in rows if not (r["date"] == date and r["series"] == series and r["metric"] == metric)]
        rows.append({"date": date, "series": series, "metric": metric, "value": value,
                     "source": source, "approx": args.approx, "note": args.note})
        _write(rows)
        print(f"Logged: {date} {series} {metric} = {value}{' (approx)' if args.approx=='1' else ''}")
        print("Now rerun: python web/build_site_data.py  (then redeploy the site)")
        return 0

    # default: summary
    print(f"{len(rows)} observations in {STORE.relative_to(ROOT)}:")
    for r in sorted(rows, key=lambda r: (r["series"], r["metric"], r["date"])):
        flag = " ~approx" if r.get("approx") == "1" else ""
        print(f"  {r['date']}  {r['series']:14s} {r['metric']:14s} {r['value']:>7}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
