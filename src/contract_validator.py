"""Deterministic data-contract validator.

Starter baseline covered null / unique / accepted_values / numeric range.
This version adds the pieces the lab asks for:

- declared *type* validation that does not let ``pd.to_numeric(errors="coerce")``
  silently hide string/type drift,
- string ``min_length``,
- contract-level *freshness* validation (``contract['freshness']``),
- ``severity`` (``critical`` / ``warning`` / ``info``) mapped to an ``action``
  (``block`` / ``quarantine`` / ``warn``).

Return shape (stable, consumed by ``student_api.validate_orders`` and the hidden
evaluation)::

    {
      "check": "unique",
      "column": "order_id",
      "severity": "critical",
      "passed": False,
      "details": "duplicate_rows=2",
      "action": "block",
    }
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}

# Default remediation action per severity. A contract/rule may override with an
# explicit ``action:`` key.
DEFAULT_ACTION = {
    "critical": "block",
    "warning": "quarantine",
    "info": "warn",
}


def _action_for(severity: str, override: str | None = None) -> str:
    if override:
        return override
    return DEFAULT_ACTION.get(severity, "warn")


def _issue(
    check: str,
    *,
    column: str | None,
    severity: str,
    passed: bool,
    details: str,
    action: str | None = None,
) -> dict[str, Any]:
    return {
        "check": check,
        "column": column,
        "severity": severity,
        "passed": bool(passed),
        "details": details,
        "action": _action_for(severity, action),
    }


def load_contract(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# --------------------------------------------------------------------------- #
# Type checking helpers
# --------------------------------------------------------------------------- #
def _type_violations(series: pd.Series, declared: str) -> tuple[int, str]:
    """Return (violation_count, note) for a declared logical type.

    We only consider non-null cells. The point is to catch drift that a naive
    ``pd.to_numeric(..., errors="coerce")`` would hide.
    """
    non_null = series.dropna()
    if non_null.empty:
        return 0, "no_non_null_values"

    declared = declared.lower()

    if declared in {"integer", "int"}:
        coerced = pd.to_numeric(non_null, errors="coerce")
        non_numeric = int(coerced.isna().sum())
        # values that are numeric but not whole numbers are also violations
        non_integer = int((coerced.dropna() % 1 != 0).sum())
        return non_numeric + non_integer, f"non_numeric={non_numeric}, non_integer={non_integer}"

    if declared in {"number", "float", "double", "numeric"}:
        coerced = pd.to_numeric(non_null, errors="coerce")
        return int(coerced.isna().sum()), "non_numeric cells"

    if declared in {"datetime", "timestamp", "date"}:
        coerced = pd.to_datetime(non_null, errors="coerce", utc=True)
        return int(coerced.isna().sum()), "unparseable datetime cells"

    if declared in {"boolean", "bool"}:
        ok = {True, False, "true", "false", "True", "False", 0, 1, "0", "1"}
        bad = int((~non_null.isin(list(ok))).sum())
        return bad, "non-boolean cells"

    if declared in {"string", "str", "varchar", "text"}:
        # Drift we care about: the column arrived as a pure numeric dtype, i.e.
        # the string identity was lost (e.g. customer_id "C0001" -> 1).
        if pd.api.types.is_bool_dtype(series) or pd.api.types.is_numeric_dtype(series):
            return int(non_null.shape[0]), "column dtype is numeric, expected string"
        return 0, "ok"

    return 0, f"unknown_declared_type={declared}"


# --------------------------------------------------------------------------- #
# Freshness
# --------------------------------------------------------------------------- #
def _check_freshness(
    df: pd.DataFrame,
    freshness: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    column = freshness.get("column")
    max_delay = float(freshness.get("max_delay_minutes", freshness.get("threshold_minutes", 60)))
    severity = freshness.get("severity", "warning")
    action = freshness.get("action")
    now = now or datetime.now(timezone.utc)

    if column not in df.columns:
        return _issue(
            "freshness",
            column=column,
            severity=severity,
            passed=False,
            details=f"freshness column '{column}' missing",
            action=action,
        )

    parsed = pd.to_datetime(df[column], errors="coerce", utc=True)
    if parsed.notna().sum() == 0:
        return _issue(
            "freshness",
            column=column,
            severity=severity,
            passed=False,
            details="no parseable timestamps in freshness column",
            action=action,
        )

    latest = parsed.max()
    age_minutes = (pd.Timestamp(now) - latest).total_seconds() / 60.0
    return _issue(
        "freshness",
        column=column,
        severity=severity,
        passed=(age_minutes <= max_delay),
        details=f"age_minutes={age_minutes:.1f}, max_delay_minutes={max_delay:.0f}",
        action=action,
    )


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def validate_dataframe(
    df: pd.DataFrame,
    contract: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    # A contract may describe columns under "columns" (orders) or "fields" (kb).
    columns = contract.get("columns") or contract.get("fields") or {}

    for column, rules in columns.items():
        rules = rules or {}
        severity = rules.get("severity", "warning")
        action = rules.get("action")
        required = bool(rules.get("required", False))

        if column not in df.columns:
            if required:
                issues.append(
                    _issue(
                        "required_column",
                        column=column,
                        severity=severity,
                        passed=False,
                        details=f"Missing required column: {column}",
                        action=action,
                    )
                )
            continue

        series = df[column]

        if required:
            null_count = int(series.isna().sum())
            issues.append(
                _issue(
                    "not_null",
                    column=column,
                    severity=severity,
                    passed=(null_count == 0),
                    details=f"null_count={null_count}",
                    action=action,
                )
            )

        if rules.get("unique"):
            duplicate_count = int(series.duplicated(keep=False).sum())
            issues.append(
                _issue(
                    "unique",
                    column=column,
                    severity=severity,
                    passed=(duplicate_count == 0),
                    details=f"duplicate_rows={duplicate_count}",
                    action=action,
                )
            )

        accepted = rules.get("accepted_values")
        if accepted is not None:
            invalid_mask = series.notna() & ~series.isin(accepted)
            invalid_count = int(invalid_mask.sum())
            issues.append(
                _issue(
                    "accepted_values",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"invalid_count={invalid_count}; accepted={accepted}",
                    action=action,
                )
            )

        if "min" in rules or "max" in rules:
            numeric = pd.to_numeric(series, errors="coerce")
            invalid = pd.Series(False, index=series.index)
            if "min" in rules:
                invalid |= numeric < rules["min"]
            if "max" in rules:
                invalid |= numeric > rules["max"]
            invalid_count = int(invalid.fillna(False).sum())
            issues.append(
                _issue(
                    "range",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"invalid_count={invalid_count}",
                    action=action,
                )
            )

        declared_type = rules.get("type")
        if declared_type:
            violations, note = _type_violations(series, str(declared_type))
            issues.append(
                _issue(
                    "type",
                    column=column,
                    severity=severity,
                    passed=(violations == 0),
                    details=f"declared={declared_type}; violations={violations} ({note})",
                    action=action,
                )
            )

        if "min_length" in rules:
            min_len = int(rules["min_length"])
            lengths = series.dropna().astype(str).str.len()
            too_short = int((lengths < min_len).sum())
            issues.append(
                _issue(
                    "min_length",
                    column=column,
                    severity=severity,
                    passed=(too_short == 0),
                    details=f"min_length={min_len}; too_short={too_short}",
                    action=action,
                )
            )

    freshness = contract.get("freshness")
    if isinstance(freshness, dict) and freshness.get("column"):
        issues.append(_check_freshness(df, freshness, now=now))

    return issues


def failed_issues(
    issues: list[dict[str, Any]], min_severity: str | None = None
) -> list[dict[str, Any]]:
    failed = [i for i in issues if not i.get("passed", False)]
    if min_severity is None:
        return failed
    threshold = SEVERITY_ORDER[min_severity]
    return [i for i in failed if SEVERITY_ORDER.get(i.get("severity", "warning"), 1) >= threshold]


def blocking_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Issues whose action would stop the pipeline."""
    return [i for i in issues if not i.get("passed", False) and i.get("action") == "block"]
