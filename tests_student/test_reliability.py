"""Student evidence tests for the upgrades made in this lab.

Each test corresponds to a failure mode the starter baseline did NOT catch.
Run: pytest tests_student -q
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from observability.openlineage_emit import build_events
from observability.slo import evaluate_burn_policy
from student_api import (
    column_downstream,
    detect_distribution,
    detect_metric,
    multiwindow_burn,
    rag_embedding_shift,
    validate_orders,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "orders_contract.yaml"


def _ts(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _df(**over):
    row = {
        "order_id": 1, "customer_id": "C1", "amount": 10.0, "currency": "USD",
        "status": "completed", "created_at": _ts(10), "updated_at": _ts(5),
    }
    row.update(over)
    row2 = dict(row, order_id=2, customer_id="C2")
    return pd.DataFrame([row, row2])


# ---- contract ----------------------------------------------------------------
def test_type_drift_on_amount_is_flagged():
    df = _df()
    df["amount"] = df["amount"].astype(object)
    df.loc[0, "amount"] = "N/A"  # string in a numeric column
    issues = [i for i in validate_orders(df, CONTRACT) if not i["passed"]]
    assert any(i["check"] == "type" and i["column"] == "amount" for i in issues)


def test_stale_batch_fails_freshness():
    df = _df(created_at=_ts(600), updated_at=_ts(600))
    issues = [i for i in validate_orders(df, CONTRACT) if not i["passed"]]
    assert any(i["check"] == "freshness" for i in issues)


def test_critical_failure_maps_to_block_action():
    df = _df()
    df.loc[1, "order_id"] = 1  # duplicate PK -> critical
    issues = [i for i in validate_orders(df, CONTRACT) if not i["passed"]]
    dup = next(i for i in issues if i["check"] == "unique")
    assert dup["severity"] == "critical" and dup["action"] == "block"


# ---- anomaly (seasonality / robustness) -------------------------------------
WEEKDAY = [600, 610, 590, 605, 600, 595, 608]
SATURDAY = [258, 260, 255, 249, 262, 257]


def test_real_70pct_drop_is_anomaly():
    assert detect_metric(180, WEEKDAY, method="auto", context={"metric_name": "row_count"})["is_anomaly"]


def test_legit_saturday_is_not_anomaly_with_segment_baseline():
    res = detect_metric(255, WEEKDAY, method="auto",
                        context={"day_of_week": 5, "same_segment_history": SATURDAY})
    assert res["is_anomaly"] is False


def test_known_event_suppresses_alert():
    res = detect_metric(120, WEEKDAY, method="auto", context={"known_event": "black_friday"})
    assert res["is_anomaly"] is False


def test_known_event_still_surfaces_underlying_signal_for_review():
    # A known campaign window must not page, but a real collapse inside it must
    # not be silently zeroed either.
    res = detect_metric(120, WEEKDAY, method="auto", context={"known_event": "black_friday"})
    assert res["is_anomaly"] is False
    assert res["needs_review"] is True
    assert res["score"] > 3.5 and res["direction"] == "drop"


def test_known_event_quiet_when_metric_is_actually_normal():
    res = detect_metric(602, WEEKDAY, method="auto", context={"known_event": "maintenance"})
    assert res["is_anomaly"] is False and res["needs_review"] is False


def test_business_day_batch_not_flagged_against_business_day_baseline():
    # Regression: `make baseline` used to raise a false anomaly every weekend
    # because a full-volume batch was compared to a same-weekday (low) segment.
    # The batch represents a business day, so that is what it is baselined on.
    business_day_history = [600, 610, 590, 605, 600, 595, 608, 603, 597, 612]
    res = detect_metric(600, business_day_history, method="auto",
                        context={"metric_name": "row_count", "batch_profile": "business_day"})
    assert res["is_anomaly"] is False


def test_mad_zero_variance_history():
    assert detect_metric(5, [5, 5, 5, 5, 5, 5], method="mad")["is_anomaly"] is False
    assert detect_metric(50, [5, 5, 5, 5, 5, 5], method="mad")["is_anomaly"] is True


# ---- distribution ----------------------------------------------------------
def test_psi_catches_shape_shift_without_mean_blowup():
    baseline = [10] * 50 + [20] * 50            # mean 15
    current = [10] * 5 + [20] * 95              # mean ~19.5, ratio only ~1.3
    res = detect_distribution(current, baseline)
    assert res["is_anomaly"] is True
    assert res["psi"] >= 0.2


# ---- SLO multi-window ----------------------------------------------------------
def test_sustained_fast_burn_pages():
    assert multiwindow_burn(20.0, 16.0)["page"] is True


def test_transient_spike_does_not_page():
    out = multiwindow_burn(20.0, 1.0)
    assert out["page"] is False and out["kind"] == "transient_spike"


# ---- column lineage -----------------------------------------------------------
def test_column_lineage_is_transitive():
    g = {
        "raw_orders.amount": ["stg_orders.amount_usd"],
        "stg_orders.amount_usd": ["fct_daily_revenue.daily_revenue"],
        "fct_daily_revenue.daily_revenue": ["ceo_revenue_dashboard.revenue"],
    }
    assert column_downstream(g, "raw_orders.amount") == [
        "stg_orders.amount_usd",
        "fct_daily_revenue.daily_revenue",
        "ceo_revenue_dashboard.revenue",
    ]


# ---- RAG embedding drift -----------------------------------------------------
def test_embedding_norm_collapse_is_detected():
    baseline = [1.0, 0.98, 1.02, 1.01, 0.99, 1.0]
    current = [0.4, 0.42, 0.38]
    assert rag_embedding_shift(current, baseline)["is_anomaly"] is True


# ---- BONUS: multi-tier burn-rate policy ------------------------------------
def test_burn_policy_pages_only_on_sustained_fast_tier():
    fast = evaluate_burn_policy({"1h": 15.0, "5m": 15.0, "6h": 2.0, "30m": 2.0, "3d": 1.0})
    assert fast["page"] is True and fast["severity"] == "critical"


def test_burn_policy_tickets_on_slow_tier_only():
    slow = evaluate_burn_policy({"1h": 2.0, "5m": 2.0, "6h": 1.5, "30m": 1.5, "3d": 1.2})
    assert slow["page"] is False and slow["ticket"] is True


def test_burn_policy_quiet_when_short_window_recovered():
    out = evaluate_burn_policy({"1h": 20.0, "5m": 0.2, "6h": 0.2, "30m": 0.2, "3d": 20.0})
    assert out["page"] is False and out["ticket"] is False


# ---- BONUS: OpenLineage events ----------------------------------------------
def test_openlineage_events_are_spec_shaped():
    events = build_events(ROOT / "data" / "baseline" / "lineage_graph.json")
    assert events, "no events emitted"
    revenue = [
        e for e in events
        if e["job"]["name"] == "build.fct_daily_revenue" and e["eventType"] == "COMPLETE"
    ]
    assert revenue
    ev = revenue[0]
    assert {"eventType", "eventTime", "run", "job", "inputs", "outputs", "producer"} <= ev.keys()
    input_names = {i["name"] for i in ev["inputs"]}
    assert {"stg_orders", "stg_customers"} <= input_names
    col_fields = ev["outputs"][0]["facets"]["columnLineage"]["fields"]
    assert "daily_revenue" in col_fields
