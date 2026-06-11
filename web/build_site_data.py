"""Generate static JSON snapshots for the Serention indices site.

Reads the derived CSVs this pipeline already produces (csv/*.csv, the estimate
composition table, and the NY Fed benchmark CSVs) and writes self-contained JSON
into web/data/. The site is fully static — no database exposed — so refreshing
the site after a monthly index rebuild is just: rerun this, then redeploy.

Run: python web/build_site_data.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "web" / "data"
DATA.mkdir(parents=True, exist_ok=True)


def _pct(x, d=2):
    return None if pd.isna(x) else round(float(x) * 100, d)


def _num(x, d=3):
    return None if pd.isna(x) else round(float(x), d)


def _int(x):
    return None if pd.isna(x) else int(round(float(x)))


def _fed(fn):
    p = ROOT / "Inputs" / "fed" / fn
    if not p.exists():
        return []
    d = pd.read_csv(p, parse_dates=["date"]).sort_values("date")
    return [{"date": r["date"].strftime("%Y-%m-%d"), "value": _num(r["value"], 3)}
            for _, r in d.iterrows()]


def load_benchmarks() -> dict:
    """Rating-agency benchmark figures logged from public disclosures (see log_benchmarks.py)."""
    p = ROOT / "Inputs" / "benchmarks" / "agency_benchmarks.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    out: dict = {}
    for (series, metric), g in df.groupby(["series", "metric"]):
        pts = [{"date": str(r["date"]) + "-01", "value": _num(r["value"], 2),
                "approx": int(r.get("approx", 0)) == 1}
               for _, r in g.sort_values("date").iterrows() if pd.notna(r["value"])]
        out.setdefault(series, {})[metric] = pts
    return out


def build_auto_subprime() -> dict:
    idx = pd.read_csv(ROOT / "csv" / "index_marks.csv", parse_dates=["month"]).sort_values("month")
    comp_path = ROOT / "csv" / "composition_estimate.csv"
    if comp_path.exists():
        comp = pd.read_csv(comp_path)
        comp = comp.rename(columns={comp.columns[0]: "month"})
        comp["month"] = pd.to_datetime(comp["month"])
        idx = idx.merge(comp[["month", "n_deals", "est_borrowers", "avg_fico"]],
                        on="month", how="left")
    for c in ("n_deals", "est_borrowers", "avg_fico"):
        if c not in idx.columns:
            idx[c] = pd.NA

    series = [{
        "date": r["month"].strftime("%Y-%m-%d"),
        "stress": _num(r.get("stress_index")),
        "delq30": _pct(r.get("delq30plus")),
        "delq60": _pct(r.get("delq60plus")),
        "roll": _pct(r.get("roll_c_to_30")),
        "net_loss": _pct(r.get("net_loss_annl")),
        "recovery": _pct(r.get("recovery_rate")),
        "n_deals": _int(r.get("n_trusts")),
        "borrowers": _int(r.get("est_borrowers")),
        "fico": _int(r.get("avg_fico")),
    } for _, r in idx.iterrows()]

    last = idx.iloc[-1]
    latest = {
        "as_of": last["month"].strftime("%Y-%m-%d"),
        "stress": _num(last.get("stress_index")),
        "delq30": _pct(last.get("delq30plus")),
        "delq60": _pct(last.get("delq60plus")),
        "net_loss": _pct(last.get("net_loss_annl")),
        "recovery": _pct(last.get("recovery_rate")),
        "n_deals": _int(last.get("n_trusts")),
        "fico": _int(last.get("avg_fico")),
        "borrowers": _int(last.get("est_borrowers")),
        "first": idx.iloc[0]["month"].strftime("%Y-%m-%d"),
    }
    return {
        "product": "Auto Subprime Credit Index",
        "ticker": "SUBA",
        "slug": "auto-subprime",
        "as_of": latest["as_of"],
        "latest": latest,
        "series": series,
        "fed": {
            "sub620_30plus_q": _fed("fed_subprime_below620_30plus.csv"),
            "auto90_annl": _fed("fed_auto_90plus_transition.csv"),
        },
        "covid": {"start": "2020-04-01", "end": "2020-12-31"},
        "agency": load_benchmarks(),
        "methodology": (
            "Loan-level, balance-weighted composite built from SEC ABS-EE filings of "
            "subprime auto securitizations (WAVG issuance FICO < 640). Components: 30+/60+ "
            "DPD, Current→30+ roll rate, annualized net loss, recovery rate. The headline "
            "stress index is a rolling-24-month z-score composite (higher = worse). "
            "Validated against NY Fed auto-credit series."
        ),
    }


def main() -> int:
    auto = build_auto_subprime()
    (DATA / "auto-subprime.json").write_text(json.dumps(auto))

    products = {
        "family": "Serention Indices",
        "products": [
            {"slug": "auto-subprime", "name": "Auto Subprime Credit Index", "ticker": "SUBA",
             "status": "live",
             "tagline": "Monthly credit-deterioration signal from subprime auto ABS loan tapes.",
             "asset_class": "Consumer ABS · Auto"},
            {"slug": "auto-prime", "name": "Prime Auto Credit Index", "ticker": "PRMA",
             "status": "planned", "tagline": "Investment-grade auto loan performance.",
             "asset_class": "Consumer ABS · Auto"},
            {"slug": "auto-lease", "name": "Auto Lease Residual Index", "ticker": "LEAS",
             "status": "planned", "tagline": "Lease residual & return performance.",
             "asset_class": "Consumer ABS · Auto"},
            {"slug": "card", "name": "Credit Card Master Trust Index", "ticker": "CARD",
             "status": "planned", "tagline": "Revolving consumer credit charge-off & delinquency.",
             "asset_class": "Consumer ABS · Cards"},
        ],
    }
    (DATA / "products.json").write_text(json.dumps(products))
    print(f"Wrote {DATA}/auto-subprime.json ({len(auto['series'])} months, "
          f"as-of {auto['as_of']}) and products.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
