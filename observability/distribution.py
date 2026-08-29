"""Distribution drift detection.

The starter only compared means. This version combines three cheap signals:

- **PSI** (Population Stability Index) over baseline quantile bins,
- **max quantile drift** (robust to outliers, unlike the mean),
- **mean ratio** (kept so extreme shifts always trip, and for continuity with
  the original behaviour).

Any one of them crossing its threshold flags an anomaly.
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np


def population_stability_index(
    current: np.ndarray, baseline: np.ndarray, bins: int = 10
) -> float:
    if baseline.size == 0 or current.size == 0:
        return 0.0
    quantiles = np.linspace(0, 1, bins + 1)
    edges = np.unique(np.quantile(baseline, quantiles))
    if edges.size < 2:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf

    base_hist, _ = np.histogram(baseline, bins=edges)
    cur_hist, _ = np.histogram(current, bins=edges)

    base_frac = base_hist / max(base_hist.sum(), 1)
    cur_frac = cur_hist / max(cur_hist.sum(), 1)

    eps = 1e-6
    base_frac = np.clip(base_frac, eps, None)
    cur_frac = np.clip(cur_frac, eps, None)
    return float(np.sum((cur_frac - base_frac) * np.log(cur_frac / base_frac)))


def _max_quantile_drift(current: np.ndarray, baseline: np.ndarray) -> float:
    qs = [0.1, 0.25, 0.5, 0.75, 0.9]
    cur_q = np.quantile(current, qs)
    base_q = np.quantile(baseline, qs)
    scale = np.std(baseline) or (np.abs(np.median(baseline)) + 1e-9)
    return float(np.max(np.abs(cur_q - base_q) / scale))


def _finite(values: Iterable[float]) -> np.ndarray:
    parsed: list[float] = []
    for v in values:
        try:
            parsed.append(float(v))
        except (TypeError, ValueError):
            continue
    arr = np.asarray(parsed, dtype=float)
    return arr[np.isfinite(arr)]


def detect_distribution_shift(
    current_values: Iterable[float],
    baseline_values: Iterable[float],
    *,
    ratio_threshold: float = 3.0,
    psi_threshold: float = 0.2,
    quantile_threshold: float = 3.0,
) -> dict[str, Any]:
    cur = _finite(current_values)
    base = _finite(baseline_values)

    # An empty *baseline* means we have nothing to compare against -> can't judge.
    if base.size == 0:
        return {
            "is_anomaly": False,
            "score": 0.0,
            "method": "distribution",
            "reason": "insufficient_baseline",
            "psi": 0.0,
            "mean_ratio": 1.0,
            "quantile_drift": 0.0,
        }
    # An empty *current* batch is itself an incident: no data arrived while the
    # baseline says data is expected. Reporting that as "healthy" would hide a
    # total ingestion failure.
    if cur.size == 0:
        return {
            "is_anomaly": True,
            "score": float("inf"),
            "method": "distribution",
            "reason": "current_batch_empty (no data arrived; baseline is non-empty)",
            "psi": float("inf"),
            "mean_ratio": float("inf"),
            "quantile_drift": float("inf"),
        }

    cur_mean = float(np.mean(cur))
    base_mean = float(np.mean(base))
    if base_mean == 0:
        mean_ratio = float("inf") if cur_mean != 0 else 1.0
    elif cur_mean == 0:
        mean_ratio = float("inf")
    else:
        mean_ratio = max(abs(cur_mean / base_mean), abs(base_mean / cur_mean))

    psi = population_stability_index(cur, base)
    qdrift = _max_quantile_drift(cur, base)

    reasons = []
    is_anomaly = False
    if mean_ratio >= ratio_threshold:
        is_anomaly = True
        reasons.append(f"mean_ratio={mean_ratio:.2f}>={ratio_threshold}")
    if psi >= psi_threshold:
        is_anomaly = True
        reasons.append(f"psi={psi:.3f}>={psi_threshold}")
    if qdrift >= quantile_threshold:
        is_anomaly = True
        reasons.append(f"quantile_drift={qdrift:.2f}>={quantile_threshold}")
    if not reasons:
        reasons.append(
            f"stable (mean_ratio={mean_ratio:.2f}, psi={psi:.3f}, quantile_drift={qdrift:.2f})"
        )

    # Normalised score: how far the worst signal is past its threshold.
    score = max(
        mean_ratio / ratio_threshold,
        psi / psi_threshold,
        qdrift / quantile_threshold,
    )

    return {
        "is_anomaly": bool(is_anomaly),
        "score": float(score),
        "method": "psi+quantile+mean_ratio",
        "reason": "; ".join(reasons),
        "psi": psi,
        "mean_ratio": mean_ratio,
        "quantile_drift": qdrift,
    }
