"""Persist the subprime auto index outputs to CSV and/or Postgres (Supabase).

Stores only the small *derived* artifacts — the per-build universe snapshot,
per-trust monthly metrics, and the monthly index marks. The raw ABS-EE XML is
never stored (it is re-fetchable from EDGAR); see ``db/schema.sql``.

CSV export has no dependencies. Database export uses ``psycopg`` (v3) and a
standard Postgres connection string, so it targets any Postgres — including a
dedicated Supabase project — via ``DATABASE_URL`` (Supabase: Project Settings
-> Database -> Connection string / URI).

Usage:
    from persist import reshape, to_csv, to_db
    to_csv(idx, trust_metrics, universe, "csv")
    to_db(idx, trust_metrics, universe, dsn=os.environ["DATABASE_URL"], as_of="2026-06-05")
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger("absee.subprime.persist")

ROOT = Path(__file__).resolve().parent

_INDEX_COLS = [
    "month", "n_trusts", "pool_balance",
    "delq30plus", "delq60plus", "roll_c_to_30", "net_loss_annl", "recovery_rate",
    "z_delq30plus", "z_delq60plus", "z_roll_c_to_30", "z_net_loss_annl", "z_recovery_rate",
    "stress_index", "covid_flag",
]
_TRUST_COLS = [
    "securitization_key", "month",
    "pool_beg_balance", "pool_end_balance", "active_balance",
    "delq30_balance", "delq60_balance", "current_balance", "roll_30_balance",
    "net_losses", "charge_offs", "recoveries",
    "delq30plus", "delq60plus", "roll_c_to_30", "net_loss_annl", "recovery_rate",
]
_UNIVERSE_COLS = [
    "as_of", "securitization_key", "shelf", "wavg_fico", "original_pool",
    "first_month", "last_month", "exited", "n_loans", "qualifies", "reason",
]


def reshape(idx: pd.DataFrame, trust_metrics: pd.DataFrame, universe: pd.DataFrame,
            as_of: str | None = None) -> dict[str, pd.DataFrame]:
    """Flatten the index outputs into DB-column-shaped frames (snake_case).

    Returns a dict with keys ``index_marks``, ``trust_metrics``, ``universe``.
    ``as_of`` (build date) is stamped on the universe + index marks; pass it in
    rather than calling a clock so the build stays reproducible.
    """
    out: dict[str, pd.DataFrame] = {}

    if idx is not None and not idx.empty:
        im = idx.reset_index().rename(columns={"index": "month"})
        if as_of is not None:
            im["as_of"] = pd.to_datetime(as_of).date()
        out["index_marks"] = im[[c for c in _INDEX_COLS if c in im.columns]
                                + (["as_of"] if as_of is not None else [])]

    if trust_metrics is not None and not trust_metrics.empty:
        tm = trust_metrics.reset_index().rename(columns={"securitizationKey": "securitization_key"})
        out["trust_metrics"] = tm[[c for c in _TRUST_COLS if c in tm.columns]]

    if universe is not None and not universe.empty:
        un = universe.reset_index().rename(columns={"securitizationKey": "securitization_key"})
        if as_of is not None:
            un["as_of"] = pd.to_datetime(as_of).date()
        out["universe"] = un[[c for c in _UNIVERSE_COLS if c in un.columns]]

    return out


def to_csv(idx, trust_metrics, universe, outdir: str | Path = "csv",
           as_of: str | None = None) -> None:
    """Write the three derived tables as CSVs under ``outdir``."""
    out = Path(outdir)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    for name, df in reshape(idx, trust_metrics, universe, as_of).items():
        path = out / f"{name}.csv"
        df.to_csv(path, index=False)
        log.info("Wrote %d rows -> %s", len(df), path)


# ---------------------------------------------------------------------------
# Postgres / Supabase
# ---------------------------------------------------------------------------

_CONFLICT_KEYS = {
    "index_marks": ["month"],
    "trust_metrics": ["securitization_key", "month"],
    "universe": ["as_of", "securitization_key"],
}


def to_db(idx, trust_metrics, universe, *, dsn: str, as_of: str | None = None,
          schema: str = "abs") -> None:
    """Upsert the derived tables into Postgres via a connection string.

    Requires ``psycopg`` (``pip install 'psycopg[binary]'``). Rows are upserted
    with ``ON CONFLICT ... DO UPDATE`` on each table's natural key, so re-runs
    are idempotent. Apply ``db/schema.sql`` to the target project first.
    """
    try:
        import psycopg  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover - depends on env
        raise RuntimeError(
            "psycopg not installed. Run: pip install 'psycopg[binary]'  "
            "(or use to_csv for a dependency-free export)."
        ) from e

    frames = reshape(idx, trust_metrics, universe, as_of)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for name, df in frames.items():
            if df.empty:
                continue
            cols = list(df.columns)
            keys = _CONFLICT_KEYS[name]
            updates = [c for c in cols if c not in keys]
            collist = ", ".join(cols)
            placeholders = ", ".join(["%s"] * len(cols))
            set_clause = ", ".join(f"{c} = excluded.{c}" for c in updates) or \
                f"{keys[0]} = excluded.{keys[0]}"
            sql = (
                f"insert into {schema}.{name} ({collist}) values ({placeholders}) "
                f"on conflict ({', '.join(keys)}) do update set {set_clause}"
            )
            rows = [tuple(None if pd.isna(v) else v for v in rec)
                    for rec in df.itertuples(index=False, name=None)]
            cur.executemany(sql, rows)
            log.info("Upserted %d rows -> %s.%s", len(rows), schema, name)
        conn.commit()
