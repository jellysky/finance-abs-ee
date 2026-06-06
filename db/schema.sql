-- Subprime Auto Credit Index — derived-data schema.
--
-- Apply to a DEDICATED Supabase project (kept separate from the funding-bot DB).
-- Stores only the small derived artifacts: the monthly index marks, per-trust
-- monthly metrics, and per-build universe snapshots. The raw ABS-EE XML is NOT
-- stored here — it is re-fetchable from EDGAR on demand and stays local.
--
-- Via Supabase: paste into the SQL editor, or apply with the MCP
-- `apply_migration` tool once this project's server is connected.

create schema if not exists abs;

-- ---------------------------------------------------------------------------
-- Per-build universe snapshot: which trusts qualified, and why/why not.
-- ---------------------------------------------------------------------------
create table if not exists abs.universe (
    as_of               date          not null,   -- build date
    securitization_key  text          not null,
    shelf               text,
    wavg_fico           numeric(6,2),
    original_pool       numeric(18,2),
    first_month         date,
    last_month          date,
    exited              date,                      -- null while live
    n_loans             integer,
    qualifies           boolean       not null,
    reason              text,                       -- failure reason if excluded
    primary key (as_of, securitization_key)
);

-- ---------------------------------------------------------------------------
-- Per-trust x month constituent metrics (ratios + dollar components, so the
-- pooled index can be re-derived exactly from this table).
-- ---------------------------------------------------------------------------
create table if not exists abs.trust_metrics (
    securitization_key  text          not null,
    month               date          not null,
    -- dollar components
    pool_beg_balance    numeric(18,2),
    pool_end_balance    numeric(18,2),
    active_balance      numeric(18,2),
    delq30_balance      numeric(18,2),
    delq60_balance      numeric(18,2),
    current_balance     numeric(18,2),
    roll_30_balance     numeric(18,2),
    net_losses          numeric(18,2),
    charge_offs         numeric(18,2),
    recoveries          numeric(18,2),
    -- derived ratios
    delq30plus          numeric(10,6),
    delq60plus          numeric(10,6),
    roll_c_to_30        numeric(10,6),
    net_loss_annl       numeric(10,6),
    recovery_rate       numeric(10,6),
    updated_at          timestamptz   not null default now(),
    primary key (securitization_key, month)
);

-- ---------------------------------------------------------------------------
-- Monthly index marks: the headline stress composite, its component levels,
-- and per-component Z-scores. This is the table an oracle / dashboard reads.
-- ---------------------------------------------------------------------------
create table if not exists abs.index_marks (
    month            date          not null primary key,
    n_trusts         integer,
    pool_balance     numeric(18,2),
    -- pooled performance components (levels)
    delq30plus       numeric(10,6),
    delq60plus       numeric(10,6),
    roll_c_to_30     numeric(10,6),
    net_loss_annl    numeric(10,6),
    recovery_rate    numeric(10,6),
    -- oriented Z-scores (higher = worse)
    z_delq30plus     numeric(10,6),
    z_delq60plus     numeric(10,6),
    z_roll_c_to_30   numeric(10,6),
    z_net_loss_annl  numeric(10,6),
    z_recovery_rate  numeric(10,6),
    -- headline
    stress_index     numeric(10,6),
    covid_flag       boolean       not null default false,
    as_of            date,                          -- build date that produced this mark
    updated_at       timestamptz   not null default now()
);

create index if not exists idx_trust_metrics_month on abs.trust_metrics (month);
create index if not exists idx_index_marks_asof on abs.index_marks (as_of);
