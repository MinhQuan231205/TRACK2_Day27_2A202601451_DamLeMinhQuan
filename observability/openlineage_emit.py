"""Emit OpenLineage-spec RunEvents from the lab lineage graph.

No SDK dependency: we build events that conform to the OpenLineage RunEvent
schema (https://openlineage.io/spec) and write them as JSONL. They can be POSTed
to a Marquez `/api/v1/lineage` endpoint or inspected offline.

For every derived dataset we emit a START + COMPLETE event for the job that
produces it, with its upstream datasets as ``inputs`` and a ``columnLineage``
output facet built from ``column_lineage`` in ``lineage_graph.json``.

Run: ``python observability/openlineage_emit.py``
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PRODUCER = "https://github.com/lab27/data-reliability-game-day"
SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
NAMESPACE = "data-reliability-lab"


def _invert(graph: dict[str, list[str]]) -> dict[str, list[str]]:
    parents: dict[str, list[str]] = defaultdict(list)
    for parent, children in graph.items():
        for child in children:
            parents[child].append(parent)
    return parents


def _column_lineage_facet(
    dataset: str, column_graph: dict[str, list[str]]
) -> dict[str, Any] | None:
    """Build an OpenLineage columnLineage facet for one output dataset."""
    parents = _invert(column_graph)
    fields: dict[str, Any] = {}
    for target_col, source_cols in parents.items():
        ds, _, col = target_col.rpartition(".")
        if ds != dataset:
            continue
        fields[col] = {
            "inputFields": [
                {
                    "namespace": NAMESPACE,
                    "name": sc.rpartition(".")[0],
                    "field": sc.rpartition(".")[2],
                }
                for sc in source_cols
            ]
        }
    if not fields:
        return None
    return {
        "_producer": PRODUCER,
        "_schemaURL": "https://openlineage.io/spec/facets/1-2-0/ColumnLineageDatasetFacet.json",
        "fields": fields,
    }


def build_events(lineage_path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(lineage_path).read_text(encoding="utf-8"))
    dataset_graph = payload.get("dataset_lineage", payload)
    column_graph = payload.get("column_lineage", {})
    parents = _invert(dataset_graph)

    events: list[dict[str, Any]] = []
    for dataset, upstreams in parents.items():
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        job = {"namespace": NAMESPACE, "name": f"build.{dataset}"}
        inputs = [{"namespace": NAMESPACE, "name": u} for u in upstreams]

        out_facets: dict[str, Any] = {}
        col_facet = _column_lineage_facet(dataset, column_graph)
        if col_facet:
            out_facets["columnLineage"] = col_facet
        outputs = [{"namespace": NAMESPACE, "name": dataset, "facets": out_facets}]

        for event_type in ("START", "COMPLETE"):
            events.append(
                {
                    "eventType": event_type,
                    "eventTime": now,
                    "run": {"runId": run_id},
                    "job": job,
                    "inputs": inputs,
                    "outputs": outputs,
                    "producer": PRODUCER,
                    "schemaURL": SCHEMA_URL,
                }
            )
    return events


def main() -> None:
    events = build_events(ROOT / "data" / "baseline" / "lineage_graph.json")
    out = ROOT / "reports" / "openlineage_events.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    jobs = sorted({e["job"]["name"] for e in events})
    print(f"Wrote {len(events)} OpenLineage events for {len(jobs)} jobs -> {out.relative_to(ROOT)}")
    for j in jobs:
        print(f"  - {j}")


if __name__ == "__main__":
    main()
