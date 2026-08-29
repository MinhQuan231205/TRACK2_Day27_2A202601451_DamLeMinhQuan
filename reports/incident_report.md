# Incident Report — Partial Orders Ingestion

> Worked example using the public `volume_drop` scenario
> (`python scripts/inject_fault.py volume_drop`). The same structure is used for
> the Phase 6 mystery incident.

## Severity
**P2** — CEO revenue dashboard understates revenue; no data corruption, pipeline
still reports `SUCCESS`. Escalates to P1 if it persists past one business day
(SLO burn, see below).

## Summary
The daily orders batch landed with only ~25% of the expected rows (150 of ~600).
All contract checks passed (the rows that arrived are individually valid), dbt
built green, but `fct_daily_revenue` and the CEO dashboard silently dropped ~75%
of revenue for the day.

## Detection
- **Signal:** `detect_metric` (row_count) in `make baseline` —
  `is_anomaly=True`, method `auto:mad+ewma`, robust score ≈ 16.5, direction `drop`.
- **Not** detected by: contract validation, GX checkpoint, dbt tests
  (`not_null` / `unique` / `accepted_values` all still pass on a smaller batch).
- **First observed:** the baseline run immediately after ingestion; anomaly is
  vs. the business-day (Mon–Fri) baseline from `data/history/metrics_history.csv`.
  The daily batch is a business-day batch, so that is the segment it is compared
  against; a healthy full-volume batch scores ≈ 0.

## Root Cause
Upstream extract delivered a truncated file (partial ingestion / early cutoff).
Row volume collapsed while every other property of the data stayed in range, so
only a volume/anomaly signal — not a deterministic rule — could catch it.

## Evidence
1. `reports/latest_metrics.json` → `orders_rows = 150` vs same-weekday baseline ≈ 600.
2. `row_count_anomaly.score` ≈ 16.5, `reason` shows `direction=drop`, EWMA level ≈ 600.
3. `failed_contract_checks = 0`, `critical_contract_failures = 0` — deterministic
   layers are blind to this failure mode.
4. dbt `build`: PASS=28 — transformation logic is fine; there is just less input.

## Blast Radius
Computed with `downstream_assets(lineage, "stg_orders")`:

```text
stg_orders
-> fct_daily_revenue
-> ceo_revenue_dashboard
```

Column-level (`column_downstream`, `raw_orders.amount`):

```text
raw_orders.amount -> stg_orders.amount_usd -> fct_daily_revenue.daily_revenue
-> ceo_revenue_dashboard.revenue
```

KB / RAG / support-agent branch is **not** affected.

## Mitigation
1. Quarantine the batch: do not publish `fct_daily_revenue` for the affected date.
2. Hold the CEO dashboard on the previous good partition; add a "data delayed" banner.
3. Re-request the full extract from upstream; re-ingest.

## Recovery
1. `make reset` (or re-ingest the complete file).
2. `make baseline` → `orders_rows` back to ≈ 600, `row_count_anomaly.is_anomaly = False`.
3. `make dbt` → 28/28 green, `assert_revenue_reconciles` passes.

## Verification
- [x] Contract healthy (`failed_contract_checks = 0`)
- [x] dbt tests healthy (`PASS=28 ERROR=0`)
- [x] anomaly returned to expected range (`is_anomaly = False`, score < 3.5)
- [x] SLO healthy / budget understood (contract_slo not breached; 1 bad ingestion
      event = burn, tracked in `kb_multiwindow_burn` style window logic)
- [x] downstream output verified (`fct_daily_revenue` daily total reconciles with
      `sum(stg_orders.amount_usd where status='completed')`)

## Prevention / Action Items
| Action | Owner | Deadline | Why |
|---|---|---|---|
| Add a volume/freshness SLO on the orders ingestion (min expected rows by weekday) | data-eng | +1 week | Turn the anomaly into an alerting SLO, not just a metric |
| Feed real per-weekday history as `context["same_segment_history"]` once the ingester emits weekend-shaped batches (today it always emits business-day volume) | data-eng | +1 week | Seasonality handling belongs in the detector, driven by real segmented history |
| dbt source freshness + row-count reconciliation test on `stg_orders` | analytics-eng | +2 weeks | Deterministic backstop if the anomaly detector regresses |
| Dashboard shows "last verified partition" + data status | bi | +2 weeks | CEO sees stale/partial state instead of a wrong number |
