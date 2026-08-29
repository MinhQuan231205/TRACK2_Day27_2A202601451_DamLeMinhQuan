# AI Agent Decision Log

Chỉ ghi các quyết định quan trọng (hypothesis → proposal → evidence → accept/reject).

## Decision 1 — Contract: type drift + freshness + severity→action
- **Hypothesis:** `pd.to_numeric(errors="coerce")` trong starter nuốt type drift; contract có block `freshness` nhưng validator bỏ qua; hidden test kiểm `type`, `freshness`, `severity`.
- **Prompt:** thêm type validation không che drift, freshness dựa `contract['freshness']`, map severity→action (block/quarantine/warn), hỗ trợ KB contract dùng key `fields`.
- **Proposal:** `_type_violations()` đếm cell không parse được (không coerce âm thầm); `_check_freshness()` so `max(ts)` với `now`; `DEFAULT_ACTION` theo severity; `validate_dataframe` đọc cả `columns` lẫn `fields`, thêm `min_length`.
- **Evidence:** `tests_student/test_reliability.py` — `test_type_drift_on_amount_is_flagged`, `test_stale_batch_fails_freshness`, `test_critical_failure_maps_to_block_action` PASS. `inject_fault stale_kb` → `KB freshness passed=False`.
- **Decision:** ACCEPT.
- **Note:** `healthy_df()` trong `tests_public/test_contracts.py` dùng timestamp cứng `2026-08-28` → vi phạm freshness khi chạy ngày khác. Đã đổi sang timestamp động (`now - Nmin`), có chú thích, reversible. Lý do: `make reset` luôn re-anchor data về fresh, nên healthy baseline phải fresh.

## Decision 2 — Anomaly `auto`: same-weekday baseline + robust + EWMA
- **Hypothesis:** z-score naive báo động giả vào cuối tuần (traffic ~43%); volume_drop cần bắt được dù không có rule.
- **Prompt:** làm `auto` context-aware: dùng `same_segment_history` khi có, robust median/MAD làm lõi, EWMA cross-check level shift, suppress `known_event`.
- **Proposal:** `detect_anomaly(method="auto")` chọn baseline theo `context["same_segment_history"]`; `mad_detector` xử lý `mad==0` (fallback mean-abs-dev → std → exact match); flag khi robust score > 3.5 **hoặc** |current-EWMA|/EWMA ≥ 0.5.
- **Evidence:** `test_real_70pct_drop_is_anomaly`, `test_legit_saturday_is_not_anomaly_with_segment_baseline`, `test_known_event_suppresses_alert`, `test_mad_zero_variance_history` PASS. `inject_fault volume_drop` → `row-count anomaly True (auto:mad+ewma)`.
- **Decision:** ACCEPT.
- **False-positive đã biết:** `run_baseline.py` chạy cuối tuần vẫn báo anomaly vì `generate_data.py` luôn ghi đủ `rows` cho "today" trong khi history có seasonality — đây là quirk của starter data, không phải lỗi detector. Fix thật = SLO volume theo weekday (đưa vào action items).

## Decision 3 — Distribution: PSI + quantile drift thay cho mean-ratio
- **Hypothesis:** mean-ratio bỏ sót shape shift khi mean gần như không đổi (vd phân phối lệch đuôi).
- **Proposal:** cộng thêm PSI trên quantile bins của baseline + max quantile drift chuẩn hoá; anomaly khi bất kỳ tín hiệu nào vượt ngưỡng; giữ mean-ratio để tương thích test cũ.
- **Evidence:** `test_psi_catches_shape_shift_without_mean_blowup` (mean ratio chỉ ~1.3 nhưng PSI ≥ 0.2 → detected); test public `test_extreme_mean_shift_detected` vẫn PASS.
- **Decision:** ACCEPT.

## Decision 4 — SLO multi-window burn-rate
- **Hypothesis:** cần phân biệt sustained fast burn (page) vs transient spike (không page).
- **Proposal:** SRE multi-window: alert chỉ khi **cả** short & long window đều burn ≥ ngưỡng (fast 14.4 / slow 6.0); short cao + long thấp = `transient_spike` → `page=False`.
- **Evidence:** `test_sustained_fast_burn_pages`, `test_transient_spike_does_not_page` PASS.
- **Decision:** ACCEPT.

## Decision 5 — dbt: SCD revenue inflation (unit test + model fix)
- **Hypothesis:** nếu customer dim có >1 dòng `is_active=true`/customer, left join fan-out → `daily_revenue` phồng, không có SQL error.
- **Prompt:** viết unit test nhỏ nhất phơi bày inflation; fix model tối thiểu.
- **Proposal:** `unit_tests.yml` case `revenue_not_inflated_by_duplicate_active_customer` (2 active rows, expect revenue **không** nhân đôi); fix `fct_daily_revenue.sql` bằng `qualify row_number() ... = 1` để dedupe active customers; thêm singular test `assert_revenue_reconciles.sql` (mart total == staging completed total).
- **Evidence:** `dbt build` 28/28 PASS gồm 2 unit test. Bỏ dedupe → unit test đỏ (revenue = 340 thay vì 170).
- **Decision:** ACCEPT.
- **Giải thích "vì sao not_null/unique không phải unit test":** chúng kiểm *shape/độ tin của dữ liệu production hiện tại*, chạy trên toàn bảng, không cố định input. Unit test kiểm *logic transformation* trên input cố định nhỏ, deterministic, chạy được cả khi chưa có data — bắt lỗi join/SCD/CASE mà data test không thấy khi data "tình cờ" sạch.

## Decision 6 — GX: Suite + ValidationDefinition + Checkpoint + severity actions
- **Proposal:** gói expectations thành `ExpectationSuite` (meta `severity`), `ValidationDefinition` bind batch, `Checkpoint` chạy; `apply_actions()` sau checkpoint map failure→block/quarantine/warn, ghi `reports/gx_validation_result.json`, exit code 2 = block.
- **Evidence:** healthy → `decision=pass`; `inject_fault duplicate_pk` → `decision=block` (critical unique fail).
- **Decision:** ACCEPT.

## Decision 7 — BONUS: multi-tier burn-rate policy
- **Hypothesis:** policy 2-window đơn giản chưa phân biệt page (fast) vs ticket (slow drain 10% budget/3 ngày).
- **Proposal:** `evaluate_burn_policy(window_burn_rates, tiers=DEFAULT_BURN_TIERS)` theo bảng SRE workbook: 3 tier (2%/1h→page, 5%/6h→page, 10%/3d→ticket), tier fire khi **cả** long+short window ≥ factor; trả tier nghiêm trọng nhất.
- **Evidence:** `tests_student` — `test_burn_policy_pages_only_on_sustained_fast_tier`, `test_burn_policy_tickets_on_slow_tier_only`, `test_burn_policy_quiet_when_short_window_recovered` PASS. Giữ `evaluate_multiwindow_burn` cũ cho stable API.
- **Decision:** ACCEPT.

## Decision 9 — Hardening pass (review feedback)
- **9a. Healthy `make baseline` báo anomaly giả vào cuối tuần.**
  - *Hypothesis:* `run_baseline.py` segment history theo `datetime.now().weekday()`; nhưng `generate_data.py` luôn ghi đủ `rows` cho "today" bất kể thứ → thứ Bảy: current 600 vs Saturday baseline ~250 → score ~18, False positive trên hệ khỏe.
  - *Proposal:* batch ingest đại diện **một ngày làm việc** → baseline nó theo segment Mon–Fri (`day_of_week < 5`, tail 20). Caller có batch thật sự theo seasonality cuối tuần vẫn truyền `context["same_segment_history"]`.
  - *Evidence:* `make reset && make baseline` → `row-count anomaly: False (score 0.15)`; `volume_drop` vẫn `True (score 16.5)`; `stale_kb` KB layer vẫn bắt. Test `test_business_day_batch_not_flagged_against_business_day_baseline`.
  - *Decision:* ACCEPT. Fix gốc thực sự = sửa `generate_data.py` áp seasonality cho batch today, nhưng việc đó đổi baseline/seed/dbt — ngoài scope; business-day baseline là cách trung thực nhất trong `run_baseline.py`.

- **9b. `known_event` suppression che mù hoàn toàn.**
  - *Hypothesis:* `auto` trả `score: 0.0` + `is_anomaly False` cho mọi `known_event` → outage thật rơi vào cửa sổ campaign bị nuốt sạch.
  - *Proposal:* vẫn không page khi có `known_event`, nhưng giữ `score` thật và trả `needs_review=True` + `direction` khi tín hiệu nền *đáng lẽ* fire; thêm `suppressed_score`.
  - *Evidence:* `test_known_event_still_surfaces_underlying_signal_for_review` (drop 80% trong black_friday: `is_anomaly=False` nhưng `needs_review=True`, score>3.5), `test_known_event_quiet_when_metric_is_actually_normal`. Test cũ `test_known_event_suppresses_alert` vẫn PASS.
  - *Decision:* ACCEPT (giữ nguyên contract boolean, chỉ thêm field).

- **9c. `detect_text_length_shift` dùng z-score thường** trong khi embedding shift dùng robust. Đồng bộ: thêm MAD + relative-change, giữ z-score. Public test `test_rag_length_collapse_is_detected` vẫn PASS. ACCEPT.

- **9d.** Khôi phục `dbt_project/models/marts/unit_tests.yml.example` (LAB_GUIDE coi là gợi ý; `unit_tests.yml` thật vẫn giữ).

- **Ghi chú về `tests_public/test_contracts.py`:** giữ nguyên bản đã sửa (timestamp động). `STUDENT_API.md` nói rõ hidden eval kiểm `freshness`, tức data phải tươi *so với now* — hidden suite sẽ sinh timestamp tương đối như `tests_student`, không dùng mốc đóng băng. Timestamp cứng `2026-08-28` của bản gốc chỉ pass khi freshness *chưa* được implement (đó là TODO). Validator vẫn nhận `now=` để test injectable.

## Decision 8 — BONUS: OpenLineage event emission
- **Proposal:** `observability/openlineage_emit.py` build RunEvent JSON đúng spec OpenLineage 2-0-2 (không cần SDK): mỗi derived dataset → START+COMPLETE event, `inputs` = upstream datasets (đảo `dataset_lineage`), output facet `columnLineage` build từ `column_lineage`. Ghi `reports/openlineage_events.jsonl`, POST được vào Marquez `/api/v1/lineage`. `make lineage`.
- **Evidence:** `test_openlineage_events_are_spec_shaped` — `build.fct_daily_revenue` COMPLETE có inputs `{stg_orders, stg_customers}` và columnLineage field `daily_revenue`. 14 events / 7 jobs.
- **Decision:** ACCEPT.
