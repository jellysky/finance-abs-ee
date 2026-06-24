# Serention — Index Methodology & Products

Serention turns public, loan-level subprime-auto disclosures into a monthly credit **index**
and a tradeable, on-chain **derivative** that settles to it. This document describes how the
index is built and what products reference it.

> Research signals derived from public SEC ABS-EE filings and public macro series. Provided for
> informational purposes only — not investment advice, an offer, or a solicitation. The on-chain
> contracts are an unaudited **testnet** proof-of-concept and are not for real funds.

---

## 1. What the index measures

A monthly, loan-level measure of how subprime auto borrowers are actually paying — built straight
from securitization tapes, not survey data. The headline series is the **Net-Loss Index (NLI)**, a
*leading* loss rate that the Net-Loss Future settles against; alongside it we publish the raw
balance-weighted credit metrics (delinquency, net loss, recovery, net yield).

## 2. Data source & pool construction

- **Source.** Loan-level **SEC ABS-EE** filings from subprime auto securitizations. Loaded to
  Postgres (`abs.loan_months`, ~33M loan-months) and flagged `in_index` per the scope below.
- **Scope.** Deals whose balance-weighted issuance FICO is **below 640** and whose original pool
  topped **$200M**. Constituents are drawn from staggered **2017–2024+** vintages across the major
  subprime shelves (Santander Drive, Exeter, AmeriCredit, Bridgecrest, Carvana). History runs back
  to **December 2016**; the live pool holds up to **~12 deals** at a time.
- **Handling seasoning.** Subprime losses follow a hump as a deal ages, which would otherwise
  dominate the signal. Two controls:
  1. The index **pools loans across many deals of staggered vintages and balance-weights them**, so
     the blended pool age stays roughly flat and the level reflects credit, not any one deal's loss
     curve.
  2. A deal **exits the pool once its balance falls below 10% of issuance**, dropping the
     surviving-bad-loan tail of a nearly paid-down deal.
- **Pooling a metric.** Every metric is computed by summing the numerator and denominator balances
  across the live deals, then dividing — so large deals carry proportionally more weight. Example
  (30+ DPD): pooled = Σ(30+ DPD balance) ÷ Σ(active balance).

## 3. The Net-Loss Index (NLI) — the settlement reference

The NLI is the **gross annualized net-loss rate, recognized early**. Two design choices make it a
*leading* indicator of realized losses:

1. **Recognize loss at the 90+ DPD pipeline, not at charge-off.** Booked charge-offs trail real
   losses by ~a year (a loan typically sits at 120+ DPD ~13 months before the loss is booked).
   Reading loss off the moment a loan **enters 90+ DPD** removes that lag.
2. **Scale by the empirical roll-to-charge-off (~0.81).** From the transition matrix over ~33M
   loan-months (`csv/roll_matrix.csv`): ~**81%** of loans that reach 90+ DPD eventually charge off
   (~**94%** by 120+ DPD). Scaling 90+ balances by 0.81 converts "entering 90+" into expected loss.
3. **Recoveries excluded.** They arrive with a long, uncertain lag, so they are not netted.

**Formula** (computed in `web/build_nli.py`, persisted to `abs.nli_monthly`):

```
NLI_t (%/yr) = 100 × 12 × 0.81 × (balance entering 90+ DPD in month t) / (beginning pool balance)
```

- `× 12` annualizes the monthly flow; `× 0.81` is the roll factor; `× 100` expresses it as a percent.
- "Entering 90+ DPD" = a loan at ≥90 DPD this month that was below 90 (or absent) in the prior
  consecutive month — i.e. a *new* entrant, not a stock.
- Output: `web/data/nli.json` (+ `csv/nli_monthly.csv`). 113 months back to Dec 2016; latest read
  (Apr 2026) ≈ **13.3%/yr**.

## 4. Supporting metrics

Recomputed monthly over the pooled universe and reported directly as percentages of the pool:
**30+ and 60+ day delinquency, Current→30+ roll rate, annualized net loss, recovery rate**, plus a
**net-yield** series (cash and accrued). Rebuilt each month as new ABS-EE filings post. The COVID
accommodation window (Apr–Dec 2020) is flagged but kept in the series.

> Note: an earlier "stress index" (a rolling z-score composite of these metrics) has been retired in
> favor of reporting the metrics and the NLI directly, in plain percentages.

## 5. Products

### 5.1 Net-Loss Future *(flagship)*

A **dated, cash-settled monthly future** on the pool's net-loss rate (the NLI).

- **Payoff (linear / DV01).** `PnL = side × notional × (rate_settle − rate_entry) / 100`. One point
  (1% on the rate) equals **1% of notional**. There is no division by the entry level — it is a
  linear point payoff, not a percentage return.
- **Direction.** Go **long to gain when losses rise** (hedging an ABS book); short to fade them.
- **Settlement in arrears.** Each contract has an immutable settlement time set to the **ABS-EE print
  date of its reference month**; once that passes it settles to the **realized print** for that
  month. Before settlement it is marked to market against the on-chain oracle.
- **Margining.** USDC-collateralized (testnet MockUSDC, 6 decimals). Initial margin 20% (2000 bps),
  maintenance 10% (1000 bps); below maintenance the holder tops up to initial margin or is
  liquidated (0.5% of notional to the liquidator).
- **The strip = a forward loss curve.** Listing consecutive monthly series (e.g. Jun / Jul / Aug
  2026) gives a forward curve of expected losses.

### 5.2 Loss-pass-through protection *(design)*

The Future hedges the *change* in the loss rate (capital-light, mark-to-market). Hedging the
*level* — protecting against a tranche of cumulative loss — is a separate, premium-paid instrument
(tranched, CDS-PAUG-style) in which the protection writer must collateralize the full tranche width.
Not yet built.

## 6. On-chain implementation (Sepolia testnet)

| Component | Address |
|---|---|
| MockUSDC (6-dec, open faucet) | `0x2A79d10E87ac92a185117ED2C0922d056421a06b` |
| Net-loss rate oracle (AggregatorV3-style, rate × 1e8) | `0xb1405f63aadf7d87d81dd6f18590bd7fd7d6e542` |
| Net-Loss Future — Jun 2026 (settles 2026-07-22) | `0xfc097ca716ebe8792364a397a11a36688c4a2620` |
| Net-Loss Future — Jul 2026 (settles 2026-08-22) | `0xb794d00bf66afcafde095552e57d51fa5f7836cc` |
| Net-Loss Future — Aug 2026 (settles 2026-09-22) | `0xe645295b7c4137f968efa596ef22adab085b7793` |

- **Contract:** `contracts/src/NetLossFuture.sol` (Foundry; 8/8 tests in
  `contracts/test/NetLossFuture.t.sol`). `SCALE = 1e10` converts `rate(×1e8) × notional(USDC, 1e6)`
  into a 6-decimal PnL. `settle(int256)` is owner-only and only callable at/after `settlementTime`.
- **Oracle:** publishes the live NLI as `rate × 1e8` via `latestRoundData`.

## 7. Backtester

`web/backtest.html` + `web/assets/backtest.js` replay the dated Future over history: pick an
underlying (defaults to the NLI), an entry month, and a **settlement month**; the tool applies the
linear DV01 payoff, marks to market monthly, models margin top-ups/calls, and reports per-side PnL,
IRR, return on capital, drawdown, and capital committed. Illustrative only.

## 8. Operations / pipeline

| Step | Script | What it does |
|---|---|---|
| Build index | `web/build_nli.py` | one heavy LAG pass over `abs.loan_months` → `abs.nli_monthly`, `nli.json`, csv |
| Push oracle | `push_nli.py` | reads latest NLI, `setAnswer(round(nli×1e8))` on the oracle (idempotent) |
| Settle futures | `settle_futures.py` | for each series in `strip.json`, if past `settlementTime` and the reference month is in `abs.nli_monthly`, `settle(int256)` to the realized print |
| Schedule | `run_monthly.sh` + `~/Library/LaunchAgents/fi.serention.netloss.plist` | runs the three steps monthly (24th, 07:00), after the ~22nd print |

Ingesting **new** ABS-EE filings for a fresh month (`load_loans.py`) is a separate, heavier
prerequisite; until a reference month's loan data is loaded, `settle_futures.py` reports that series
as awaiting data rather than settling to stale figures.

## 9. Validation

Cross-checked against public macro and rating-agency series — co-movement and timing are the signal,
since definitions and levels differ:

- **NY Fed** subprime (<620) 30+ delinquency flow and all-auto 90+ transition.
- **KBRA Non-Prime** and **Fitch Subprime** auto-ABS net-loss indices (logged from public monthly
  disclosures). Our deeper <640 cut runs a touch higher, as expected.
