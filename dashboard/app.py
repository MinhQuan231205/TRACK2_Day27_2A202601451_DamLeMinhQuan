from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "latest_metrics.json"
HISTORY = ROOT / "data" / "history" / "metrics_history.csv"

st.set_page_config(page_title="Data Reliability Lab", layout="wide")
st.title("Data Reliability Game Day")
st.caption("Starter dashboard - improve it only if it helps incident decisions.")

if not REPORT.exists():
    st.warning("Run `make baseline` first to generate reports/latest_metrics.json")
    st.stop()

report = json.loads(REPORT.read_text(encoding="utf-8"))

c1, c2, c3, c4 = st.columns(4)
c1.metric("Orders rows", report["orders_rows"])
c2.metric("Freshness (min)", f"{report['freshness_minutes']:.1f}")
c3.metric("Contract failures", report["failed_contract_checks"])
c4.metric("Critical failures", report["critical_contract_failures"])

st.subheader("Current signals")
st.json({
    "row_count_anomaly": report["row_count_anomaly"],
    "kb_text_length_signal": report["kb_text_length_signal"],
    "kb_freshness": report.get("kb_freshness"),
    "contract_slo": report["contract_slo"],
    "kb_freshness_slo": report.get("kb_freshness_slo"),
    "kb_multiwindow_burn": report.get("kb_multiwindow_burn"),
})

blocking = report.get("blocking_contract_failures") or []
if blocking:
    st.error(f"{len(blocking)} BLOCKING contract failure(s) — pipeline should be halted")
    st.json(blocking)
burn = report.get("kb_multiwindow_burn") or {}
if burn.get("page"):
    st.error(f"PAGE: {burn.get('reason')}")

history = pd.read_csv(HISTORY)
st.subheader("Historical row count")
st.line_chart(history.set_index("date")[["row_count"]])

st.subheader("Example blast radius")
st.write("stg_orders -> " + " -> ".join(report["sample_blast_radius_from_stg_orders"]))

slo = report.get("kb_freshness_slo") or {}
if slo:
    st.subheader("KB freshness SLO")
    s1, s2, s3 = st.columns(3)
    s1.metric("Target", slo.get("target"))
    s2.metric("Burn rate", f"{slo.get('burn_rate', 0):.2f}")
    s3.metric("Error budget left", f"{slo.get('remaining_error_budget_fraction', 1) * 100:.0f}%")
