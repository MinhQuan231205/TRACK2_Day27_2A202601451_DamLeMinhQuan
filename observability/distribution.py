"""Two-sample distribution drift detection.

The starter only compared means, which misses equal-mean shape/scale shifts and
blows up to ``inf`` when the baseline mean is near zero. This version combines
three cheap, bounded signals:

- **standardised mean shift** ``|mean_c - mean_b| / std_b`` (effect size, not a
  raw ratio),
- **PSI** (Population Stability Index) over baseline quantile bins,
- **max quantile drift** (robust to outliers).

Any one crossing its threshold flags an anomaly. An empty current batch -- or one
whose every value is non-finite -- against a real baseline is itself an anomaly,
reported with a finite score.
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np

# Upper bound for every score this module reports. An empty / all-non-finite
# current batch is a genuine anomaly, but callers (dashboards, JSON reports,
# downstream `max(...)`) need a *finite* number, never ``inf`` or ``nan``.
_MAX_SCORE = 1.0e12


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


def _finite(values: Iterable[float]) -> tuple[np.ndarray, int]:
    """Coerce to float, keep only finite values. Returns (finite_array, n_dropped).

    ``n_dropped`` counts values that were non-numeric or non-finite (NaN / inf) --
    a current batch that is entirely such values is an invalid sample, not "no
    opinion".
    """
    parsed: list[float] = []
    dropped = 0
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            dropped += 1
            continue
        if np.isfinite(f):
            parsed.append(f)
        else:
            dropped += 1
    return np.asarray(parsed, dtype=float), dropped


def detect_distribution_shift(
    current_values: Iterable[float],
    baseline_values: Iterable[float],
    *,
    ratio_threshold: float = 3.0,
    psi_threshold: float = 0.2,
    quantile_threshold: float = 3.0,
) -> dict[str, Any]:
    cur, dropped_cur = _finite(current_values)
    base, dropped_base = _finite(baseline_values)

    # An empty *baseline* means we have nothing to compare against -> can't judge.
    if base.size == 0:
        return {
            "is_anomaly": False,
            "score": 0.0,
            "method": "distribution",
            "reason": f"insufficient_baseline (baseline_count=0, dropped_baseline={dropped_base})",
            "psi": 0.0,
            "mean_ratio": 1.0,
            "quantile_drift": 0.0,
        }
    # An empty *current* batch -- or one whose every value was non-finite -- is
    # itself an incident: no usable data arrived while the baseline says data is
    # expected. Reporting that as "healthy" would hide a total ingestion failure.
    # The score must stay finite so JSON reports / downstream max() don't break.
    if cur.size == 0:
        return {
            "is_anomaly": True,
            "score": _MAX_SCORE,
            "method": "distribution",
            "reason": (
                "current_batch_empty_or_all_non_finite "
                f"(usable_current=0, dropped_current={dropped_cur}, baseline_count={base.size})"
            ),
            "psi": _MAX_SCORE,
            "mean_ratio": _MAX_SCORE,
            "quantile_drift": _MAX_SCORE,
        }

    cur_mean = float(np.mean(cur))
    base_mean = float(np.mean(base))

    # Location signal. A *standardised* shift (|Δmean| / std_baseline) instead of
    # a raw ratio: a ratio explodes to inf when the baseline mean sits near zero
    # (centered residuals, deltas, log-ratios) and would then flag an arbitrarily
    # small move. Kept alongside a bounded ratio for output continuity.
    base_spread = float(np.std(base))
    if base_spread == 0.0:
        base_spread = max(abs(base_mean) * 0.01, 1e-9)
    std_mean_shift = min(abs(cur_mean - base_mean) / base_spread, _MAX_SCORE)

    if abs(base_mean) > 1e-9 and abs(cur_mean) > 1e-9:
        mean_ratio = min(max(abs(cur_mean / base_mean), abs(base_mean / cur_mean)), _MAX_SCORE)
    else:
        mean_ratio = 1.0

    psi = min(population_stability_index(cur, base), _MAX_SCORE)
    qdrift = min(_max_quantile_drift(cur, base), _MAX_SCORE)

    reasons = []
    is_anomaly = False
    if std_mean_shift >= ratio_threshold:
        is_anomaly = True
        reasons.append(f"std_mean_shift={std_mean_shift:.2f}>={ratio_threshold}")
    if psi >= psi_threshold:
        is_anomaly = True
        reasons.append(f"psi={psi:.3f}>={psi_threshold}")
    if qdrift >= quantile_threshold:
        is_anomaly = True
        reasons.append(f"quantile_drift={qdrift:.2f}>={quantile_threshold}")
    if not reasons:
        reasons.append(
            f"stable (std_mean_shift={std_mean_shift:.2f}, psi={psi:.3f}, "
            f"quantile_drift={qdrift:.2f})"
        )

    # Normalised score: how far the worst signal is past its threshold. Bounded.
    score = min(
        max(
            std_mean_shift / ratio_threshold,
            psi / psi_threshold,
            qdrift / quantile_threshold,
        ),
        _MAX_SCORE,
    )

    return {
        "is_anomaly": bool(is_anomaly),
        "score": float(score),
        "method": "std_shift+psi+quantile",
        "reason": "; ".join(reasons),
        "psi": float(psi),
        "mean_ratio": float(mean_ratio),
        "std_mean_shift": float(std_mean_shift),
        "quantile_drift": float(qdrift),
    }
