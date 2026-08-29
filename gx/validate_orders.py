#!/usr/bin/env python3
"""Great Expectations Core 1.21 validation flow for the orders dataset.

This goes beyond running loose expectations:

1. build a reusable **Expectation Suite** (with per-expectation ``severity`` meta),
2. wrap it in a **Validation Definition** bound to a batch,
3. run it from a **Checkpoint**,
4. apply **severity-aware actions**: a ``critical`` failure -> ``block``,
   a ``warning`` failure -> ``quarantine``, otherwise ``warn``. The decision and
   the full result are written to ``reports/gx_validation_result.json``.

Run: ``python gx/validate_orders.py``  (exit code 2 means "block").
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import great_expectations as gx
    from great_expectations import expectations as gxe
    from great_expectations.core.expectation_suite import ExpectationSuite
    from great_expectations.core.validation_definition import ValidationDefinition
    from great_expectations.checkpoint.checkpoint import Checkpoint
except ImportError as exc:  # friendlier classroom failure
    raise SystemExit("great_expectations is not installed. Run: pip install -r requirements.txt") from exc


SEVERITY_ACTION = {"critical": "block", "warning": "quarantine", "info": "warn"}


def build_suite(context) -> ExpectationSuite:
    suite = context.suites.add_or_update(ExpectationSuite(name="orders_contract_suite"))
    suite.expectations = []

    critical = {"severity": "critical"}
    warning = {"severity": "warning"}

    for exp in [
        gxe.ExpectColumnValuesToNotBeNull(column="order_id", meta=critical),
        gxe.ExpectColumnValuesToBeUnique(column="order_id", meta=critical),
        gxe.ExpectColumnValuesToNotBeNull(column="customer_id", meta=critical),
        gxe.ExpectColumnValuesToNotBeNull(column="amount", meta=critical),
        gxe.ExpectColumnValuesToBeBetween(column="amount", min_value=0, meta=critical),
        gxe.ExpectColumnValuesToBeInSet(
            column="currency", value_set=["USD", "VND"], meta=critical
        ),
        gxe.ExpectColumnValuesToBeInSet(
            column="status",
            value_set=["pending", "completed", "refunded", "cancelled"],
            meta=warning,
        ),
        gxe.ExpectColumnValuesToNotBeNull(column="created_at", meta=critical),
        gxe.ExpectColumnValuesToNotBeNull(column="updated_at", meta=critical),
    ]:
        suite.add_expectation(exp)
    return suite


def apply_actions(checkpoint_result) -> dict:
    """Severity-aware 'actions' step run after the checkpoint."""
    failures = []
    for suite_result in checkpoint_result.run_results.values():
        for res in suite_result.results:
            if res.success:
                continue
            cfg = res.expectation_config
            severity = (cfg.meta or {}).get("severity", "warning")
            failures.append(
                {
                    "expectation": cfg.type,
                    "column": cfg.kwargs.get("column"),
                    "severity": severity,
                    "action": SEVERITY_ACTION.get(severity, "warn"),
                }
            )

    if any(f["action"] == "block" for f in failures):
        decision = "block"
    elif any(f["action"] == "quarantine" for f in failures):
        decision = "quarantine"
    elif failures:
        decision = "warn"
    else:
        decision = "pass"

    return {"decision": decision, "failures": failures}


def main() -> int:
    df = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    context = gx.get_context(mode="ephemeral")

    data_source = context.data_sources.add_pandas("orders_pandas")
    asset = data_source.add_dataframe_asset(name="orders_dataframe")
    batch_definition = asset.add_batch_definition_whole_dataframe("whole_orders")

    suite = build_suite(context)

    validation_definition = context.validation_definitions.add_or_update(
        ValidationDefinition(
            name="orders_validation", data=batch_definition, suite=suite
        )
    )
    checkpoint = context.checkpoints.add_or_update(
        Checkpoint(
            name="orders_checkpoint",
            validation_definitions=[validation_definition],
            result_format={"result_format": "SUMMARY"},
        )
    )

    result = checkpoint.run(batch_parameters={"dataframe": df})
    actions = apply_actions(result)

    out = ROOT / "reports" / "gx_validation_result.json"
    out.write_text(
        json.dumps({"success": bool(result.success), "actions": actions}, indent=2, default=str),
        encoding="utf-8",
    )

    print(f"GX checkpoint success : {result.success}")
    print(f"decision              : {actions['decision']}")
    for f in actions["failures"]:
        print(f"  - {f['severity']:<8} {f['expectation']} {f['column']} -> {f['action']}")
    print(f"result written        : {out.relative_to(ROOT)}")

    return 2 if actions["decision"] == "block" else 0


if __name__ == "__main__":
    raise SystemExit(main())
