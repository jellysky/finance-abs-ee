# Subprime Auto Credit Index — Design Plan

Plan for constructing a monthly index from ABS-EE asset-level data that is
sensitive to deterioration in the subprime auto credit ecosystem
(constituents: Santander, AmeriCredit / GM Financial subprime, Exeter,
Bridgecrest, Carvana N-series, and any other subprime auto issuer that
files ABS-EE).

The downloader and cleanup pipeline (`utility.py`, `autoLoanParser.py`,
`autoLeaseParser.py`, `main.py`) already produce the inputs this plan
consumes — per-loan, per-month rows with derived fields like
`monthsDelinquent`, `principalPrepaid`, `netLosses`, `primeIndicator`, and
`reportingPeriodActualEndBalanceAmount`.

---

## What the index should actually represent

Two distinct concepts get conflated as "credit index":

- **Performance index** — a faithful summary of what's happening in subprime
  auto right now (a benchmark level).
- **Stress index** — a normalized signal that says whether things are
  deteriorating *relative to baseline* (like VIX, or a rolling Z-score).

The stated goal — "sensitivity to deterioration" — requires the second.
The first is a useful intermediate, but a raw performance index will read
"high" simply because subprime auto runs at 5–7% net losses in normal
conditions, so without normalization a level isn't actionable.

**Recommendation:** build the performance index first as a deterministic
monthly composite, then layer the stress index on top as a Z-score / percentile
transformation. The separation lets each layer be debugged independently.

---

## Five design choices, in order of impact

### 1. Universe definition

Define the universe by a *rule*, not a hand-picked list, so the universe
self-updates as new subprime trusts file ABS-EE:

- Filter to auto loans (not leases), publicly issuing depositors.
- WAVG `consumerCreditScore` at issuance below a cutoff.
  - Start at **<640** (conventional subprime line).
  - <620 captures deep subprime; ≤660 picks up bottom-of-near-prime.
- Minimum size filter (e.g. original pool > $200M) to avoid small,
  idiosyncratic deals dominating the signal.

Known qualifying issuers visible in `Inputs/dtABS.csv`:
- Santander Drive Auto Receivables Trust
- AmeriCredit Automobile Receivables Trust (GM Financial subprime)
- Exeter Automobile Receivables Trust
- Bridgecrest Lending Auto Securitization Trust (DriveTime)
- Carvana Auto Receivables Trust (N-series; the P-series is prime)
- Possibly Westlake / Flagship / Foursight if they file

The qualifying set must be derivable from the data, not hardcoded.

### 2. Loan-level vs deal-level aggregation

This is the static-pool problem. Each ABS-EE trust is a closed pool that
only shrinks over time, so an aging 2019 deal will show worse stats just
from survival selection (the loans that paid off are gone; the bad ones
remain). Two clean approaches:

- **Vintage-normalized**: compute each deal's metrics at "month-since-cutoff
  = N" and aggregate across deals at the same age. Removes seasoning bias
  but reduces constituent count at any given month.
- **Loan-level pooled**: dissolve deal boundaries; recompute on the
  underlying loan universe. Cleaner econometrically; loses the ability to
  attribute moves to specific deals.

**Recommendation:** start with *loan-level pooled* for the headline index,
keep *vintage-normalized* as a sub-index for attribution.

### 3. Which metrics go into the composite

Mix leading and lagging so the index doesn't over-react to single signals:

| Signal | Lead/lag | Why include |
|---|---|---|
| 30+ delinquency rate (balance-weighted) | leading | First sign of stress |
| **Roll rate Current → 30+ DPD** | most leading | Captures flow, not stock |
| 60+ delinquency rate | leading | Industry-standard headline |
| Annualized net loss rate | coincident | Dollars actually lost |
| Recovery rate (recoveries / charge-offs) | severity | Tells LGD direction |
| Extension / modification rate | hidden-stress | If recoverable from data |

The roll rate is the highest-value addition vs. typical industry trackers,
and `main.create_rollrates_matrix` already produces it.

### 4. Weighting

- **Balance-weighted** (current outstanding pool size) — recommended default.
- Equal-weight across trusts gives small/risky deals outsized influence.
- Original-issuance-weighted freezes constituent influence at deal launch,
  which is a bug not a feature for a deterioration signal.

### 5. Normalization for the stress layer

For each component metric:

- Compute a *rolling 24-month baseline* of mean + stdev.
- Compute the current month's Z-score against that baseline.
- Equal-weight the Z-scores into a composite, or weight by inverse
  historical volatility so noisier metrics don't dominate.
- Cap at e.g. ±3σ to prevent single outlier months from blowing up the level.

The output hovers near 0 in normal periods and spikes positive when
subprime is deteriorating.

---

## Practical traps to plan for

- **Reporting lag.** ABS-EE filings land 15–25 days after period-end.
  The index for month *M* isn't computable until late month *M+1*.
- **Survivorship.** Trusts exit when they pay down. Drop them from the
  universe when current balance falls below ~10% of original — otherwise
  they skew metrics with surviving-bad-loans selection.
- **Issuer charge-off timing differences.** Some servicers charge off at
  90 DPD, some at 120, some hold longer. Roll-rate and combined
  delinquency+default metrics are insensitive to this; raw net loss rate
  should be treated with more caution.
- **COVID artifacts.** Forbearance / accommodation programs in 2020 make
  Apr–Dec 2020 a structural break, not a market signal. Either exclude
  that window from the baseline or flag it explicitly.
- **Backfill horizon.** ABS-EE filings start in 2016, so the index has
  ~9 years of monthly history — enough for a normal-cycle baseline but
  it does *not* contain the 2008–2010 GFC stress.

---

## Validation

Before declaring the index "working," it should:

1. Reproduce known stress periods qualitatively — Q2 2020 spike then
   accommodation-driven dip; gradual rise through 2023–2024 as subprime
   auto delinquencies climbed.
2. Lead — not coincide with — the public delinquency stats from the New
   York Fed Household Debt Report.
3. Correlate but not collapse-to-equivalence with ICE BofA HY auto OAS
   spreads (or KBRA's quarterly subprime auto trend reports as a fallback).

---

## Recommended v1 implementation

The right minimal slice:

1. **`universe.py`** — derive the qualifying trust list from the data, with
   the FICO + size filters as parameters. Returns
   `(secname, qualified_from_date, exited_date)` per trust.
2. **`metrics.py`** — for each trust × month, compute the 4–5 core
   constituent metrics. Builds on `main.create_performance` but indexed
   for time series rather than per-axis.
3. **`index.py`** — loan-level pooled balance-weighted composite (the
   performance layer), then the rolling-Z-score stress layer. Produces
   a single monthly time series with attributable components.
4. **Backtest plot** vs. NY Fed HHD subprime auto delinquency series for
   sanity-check; document deviations.

---

## Open questions — RESOLVED 2026-06-05

- **FICO cutoff:** **< 640** (conventional subprime line; maps to
  `primeIndicator == 1`).
- **Stress index orientation:** **higher = worse** (deterioration index;
  ~0 in normal periods, spikes positive under stress).
- **COVID handling:** **flag, keep in baseline** (Apr–Dec 2020 marked via a
  `covid_flag` column and shaded in plots; not dropped or interpolated).
- **Output cadence:** **monthly only** for v1 (daily freshness-adjusted level
  deferred to v2).
- **Asset class scope:** **subprime auto loans only** (no lease deals in v1).

## v1 build status — 2026-06-05

Implemented and unit-tested (`test_subprime_index.py`, 6/6 passing including a
real-XML smoke test):

- `universe.py` — `build_universe` (FICO + pool-size rule, per-trust exit
  month) and `apply_universe` (filter + drop post-exit months).
- `metrics.py` — `trust_month_metrics`: per-trust × month delinquency shares,
  Current→30 roll rate, annualized net loss, recovery rate, plus the dollar
  numerators/denominators so pooling is exact.
- `index.py` — `pool_metrics` (loan-level pooled, balance-weighted performance
  layer) + `build_stress_index` (rolling-24m Z-score, ±3σ cap, inverse-vol
  weighting, higher=worse) and an end-to-end `build_index`.
- `backtest.py` — `plot_index` (stress composite + components, COVID shaded)
  and a NY Fed series loader hook for the validation overlay.
- `fetch_subprime.py` — scoped downloader for selected subprime shelves.

Validation against the NY Fed HHD subprime-auto series (item above) is pending
real-data acquisition beyond the initial scoped Santander/Exeter pull.
