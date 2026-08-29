#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from observability.anomaly import detect_anomaly
from observability.lineage import get_downstream_assets
from observability.rag_metrics import detect_text_length_shift
from observability.slo import calculate_slo, evaluate_multiwindow_burn
from src.contract_validator import (
    blocking_issues,
    failed_issues,
    load_contract,
    validate_dataframe,
)
from src.io_utils import load_jsonl


def main() -> None:
    orders = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    history = pd.read_csv(ROOT / "data" / "history" / "metrics_history.csv")
    contract = load_contract(ROOT / "contracts" / "orders_contract.yaml")
    issues = validate_dataframe(orders, contract)
    failed = failed_issues(issues)
    critical_failed = failed_issues(issues, min_severity="critical")

    # The daily ingestion batch is generated at *business-day* volume: see
    # scripts/generate_data.py — "today" always receives the full `rows`, whatever
    # weekday the class is run on, while the history carries weekend seasonality
    # (~43% volume). Baselining that batch against a same-weekday segment would
    # therefore raise a false anomaly every weekend on a perfectly healthy batch.
    # So we compare it against the Mon–Fri (business-day) history segment, which
    # is what the batch actually represents. A caller whose batch genuinely
    # follows weekend seasonality should pass context["same_segment_history"].
    current_dow = datetime.now().weekday()
    business_day_history = (
        history.loc[history["day_of_week"] < 5, "row_count"].tail(20).tolist()
    )
    row_history = (
        business_day_history
        if len(business_day_history) >= 3
        else history["row_count"].tail(14).tolist()
    )
    row_result = detect_anomaly(
        len(orders),
        row_history,
        method="auto",
        context={
            "metric_name": "row_count",
            "day_of_week": current_dow,
            "batch_profile": "business_day",
        },
    )

    updated = pd.to_datetime(orders["updated_at"], utc=True, errors="coerce")
    freshness_minutes = (
        pd.Timestamp(datetime.now(timezone.utc)) - updated.max()
    ).total_seconds() / 60.0

    docs = load_jsonl(ROOT / "data" / "incoming" / "kb_documents.jsonl")
    text_result = detect_text_length_shift(
        [d["content"] for d in docs], history["mean_text_length"].tail(14).tolist()
    )

    # KB contract + freshness (stale_kb scenario lives here).
    kb_contract = load_contract(ROOT / "contracts" / "kb_contract.yaml")
    kb_issues = validate_dataframe(pd.DataFrame(docs), kb_contract)
    kb_failed = failed_issues(kb_issues)
    kb_fresh_issue = next((i for i in kb_issues if i["check"] == "freshness"), None)

    # Demo SLO: one check event for this run.
    bad = 1 if critical_failed else 0
    contract_slo = calculate_slo(0.999, bad_events=bad, total_events=1)

    # KB freshness SLO + a multi-window burn read (short = this run, long = 7-run avg proxy).
    kb_bad = 0 if (kb_fresh_issue and kb_fresh_issue["passed"]) else 1
    kb_slo = calculate_slo(0.99, bad_events=kb_bad, total_events=1)
    burn = evaluate_multiwindow_burn(
        short_window_burn=kb_slo["burn_rate"],
        long_window_burn=kb_slo["burn_rate"],
    )

    with open(ROOT / "data" / "baseline" / "lineage_graph.json", "r", encoding="utf-8") as f:
        lineage = json.load(f)["dataset_lineage"]
    blast_radius = get_downstream_assets(lineage, "stg_orders")

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "orders_rows": int(len(orders)),
        "failed_contract_checks": len(failed),
        "critical_contract_failures": len(critical_failed),
        "blocking_contract_failures": [i for i in blocking_issues(issues)],
        "row_count_anomaly": row_result,
        "freshness_minutes": freshness_minutes,
        "kb_text_length_signal": text_result,
        "kb_failed_contract_checks": len(kb_failed),
        "kb_freshness": kb_fresh_issue,
        "kb_freshness_slo": kb_slo,
        "kb_multiwindow_burn": burn,
        "contract_slo": contract_slo,
        "sample_blast_radius_from_stg_orders": blast_radius,
    }
    out = ROOT / "reports" / "latest_metrics.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("=== DATA RELIABILITY BASELINE ===")
    print(f"orders rows              : {len(orders)}")
    print(f"contract failed checks   : {len(failed)}")
    print(f"critical contract fails  : {len(critical_failed)}")
    print(f"row-count anomaly        : {row_result['is_anomaly']} ({row_result['method']}, score={row_result['score']:.2f})")
    print(f"freshness minutes        : {freshness_minutes:.1f}")
    print(f"KB length anomaly        : {text_result['is_anomaly']}")
    print(f"KB contract failed       : {len(kb_failed)}")
    if kb_fresh_issue:
        print(f"KB freshness             : passed={kb_fresh_issue['passed']} ({kb_fresh_issue['details']})")
    print(f"KB burn page             : {burn['page']} ({burn['kind']})")
    print(f"sample blast radius      : {', '.join(blast_radius)}")
    print(f"report                    : {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
