from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any


def load_graph(path: str | Path) -> dict[str, list[str]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload["dataset_lineage"] if "dataset_lineage" in payload else payload


def get_downstream_assets(graph: dict[str, list[str]], start: str) -> list[str]:
    """Return transitive downstream assets in BFS order, excluding start."""
    seen = {start}
    q: deque[str] = deque([start])
    out: list[str] = []
    while q:
        node = q.popleft()
        for child in graph.get(node, []):
            if child not in seen:
                seen.add(child)
                out.append(child)
                q.append(child)
    return out


def get_column_downstream(
    column_graph: dict[str, list[str]], start_column: str
) -> list[str]:
    """Transitive downstream columns in BFS order, excluding the start column."""
    return get_downstream_assets(column_graph, start_column)


def load_column_graph(path: str | Path) -> dict[str, list[str]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("column_lineage", payload)


def extract_dbt_dataset_graph(manifest_path: str | Path) -> dict[str, list[str]]:
    """Minimal dbt manifest parser.

    It maps each dbt node unique_id to the nodes that depend on it. Students may
    enrich names, exposures, owners, columns, or OpenLineage facets.
    """
    path = Path(manifest_path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    def is_data_asset(node_id: str) -> bool:
        # keep models / seeds / sources / snapshots / exposures; drop test nodes
        return not node_id.startswith(("test.", "unit_test."))

    graph: dict[str, list[str]] = {}
    child_map = manifest.get("child_map", {})
    for parent, children in child_map.items():
        if not is_data_asset(parent):
            continue
        graph[parent] = [c for c in children if is_data_asset(c)]
    return graph
