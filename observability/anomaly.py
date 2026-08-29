"""Anomaly detection.

- ``zscore``: classic mean/std z-score (kept as the simple baseline).
- ``mad``: robust modified z-score (median / MAD), with sane zero-MAD handling.
- ``auto``: context-aware. Uses a same-segment (e.g. same-weekday) baseline when
  the caller provides one, prefers the robust estimator, suppresses known events,
  and falls back to z-score when history is too short.

All detectors return ``{is_anomaly, score, method, reason}``.
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np


def _as_array(history: Iterable[float]) -> np.ndarray:
    return np.asarray(list(history), dtype=float)


def zscore_detector(current: float, history: Iterable[float], threshold: float = 3.0) -> dict[str, Any]:
    values = _as_array(history)
    if values.size < 3:
        return {"is_anomaly": False, "score": 0.0, "method": "zscore", "reason": "insufficient_history"}
    mean = float(np.mean(values))
    std = float(np.std(values))
    if std == 0:
        score = float("inf") if float(current) != mean else 0.0
    else:
        score = abs(float(current) - mean) / std
    return {
        "is_anomaly": bool(score > threshold),
        "score": float(score),
        "method": "zscore",
        "reason": f"mean={mean:.3f}, std={std:.3f}, threshold={threshold}",
    }


def mad_detector(current: float, history: Iterable[float], threshold: float = 3.5) -> dict[str, Any]:
    """Robust modified z-score using median and MAD.

    Zero-MAD (many identical values) is handled by falling back to the mean
    absolute deviation, then to the standard deviation, then to an exact-match
    comparison.
    """
    values = _as_array(history)
    if values.size < 5:
        fallback = zscore_detector(current, history)
        fallback["method"] = "mad->zscore"
        fallback["reason"] = "insufficient_history_for_mad; " + fallback["reason"]
        return fallback

    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad > 0:
        scale = 1.4826 * mad
        scale_note = f"mad={mad:.3f}"
    else:
        mean_abs_dev = float(np.mean(np.abs(values - median)))
        std = float(np.std(values))
        scale = mean_abs_dev or std
        scale_note = f"mad=0 fallback_scale={scale:.3f}"

    if scale == 0:
        is_anom = float(current) != median
        return {
            "is_anomaly": bool(is_anom),
            "score": float("inf") if is_anom else 0.0,
            "method": "mad",
            "reason": f"zero-variance history at {median:.3f}",
        }

    score = abs(float(current) - median) / scale
    return {
        "is_anomaly": bool(score > threshold),
        "score": float(score),
        "method": "mad",
        "reason": f"median={median:.3f}, {scale_note}, threshold={threshold}",
    }


def _ewma_last(values: np.ndarray, alpha: float = 0.4) -> float:
    ewma = values[0]
    for v in values[1:]:
        ewma = alpha * v + (1 - alpha) * ewma
    return float(ewma)


def detect_anomaly(
    current: float,
    history: Iterable[float],
    *,
    method: str = "auto",
    threshold: float = 3.0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stable lab API. See module docstring."""
    context = context or {}

    if method == "zscore":
        return zscore_detector(current, history, threshold=threshold)
    if method == "mad":
        return mad_detector(current, history)
    if method != "auto":
        raise ValueError(f"Unsupported method: {method}")

    # ---- auto ----------------------------------------------------------------
    # 1. A caller-provided same-segment baseline (e.g. same weekday) wins: it
    #    removes the seasonality that would otherwise blow up a naive detector.
    seg = context.get("same_segment_history")
    if seg is not None and len(list(seg)) >= 3:
        base = _as_array(seg)
        base_note = "same_segment_history"
    else:
        base = _as_array(history)
        base_note = "raw_history"

    metric_name = context.get("metric_name", "metric")
    known_event = context.get("known_event")

    if base.size < 3:
        return {
            "is_anomaly": False,
            "score": 0.0,
            "method": "auto:insufficient_history",
            "reason": f"only {base.size} baseline points",
        }

    # 2. Robust estimator as the primary signal.
    result = mad_detector(current, base, threshold=3.5)

    # 3. Cross-check with EWMA relative change to catch slow drift / level shifts
    #    that a symmetric robust score can under-weight.
    ewma = _ewma_last(base)
    rel_change = abs(float(current) - ewma) / (abs(ewma) + 1e-9)
    ewma_flag = rel_change >= 0.5  # >=50% away from the smoothed level

    is_anomaly = bool(result["is_anomaly"] or ewma_flag)
    score = float(max(result["score"], rel_change))
    direction = "drop" if float(current) < float(np.median(base)) else "spike"

    # 4. Known maintenance / campaign windows are expected deviations, so we do
    #    not page on them. But suppression is not the same as blindness: we keep
    #    the real score and, when the underlying signal *would* have fired, hand
    #    back `needs_review=True` and the direction so a human still sees it. That
    #    way a genuine outage that happens to land inside a campaign window is not
    #    silently zeroed the way the old `score: 0.0` short-circuit did.
    if known_event:
        return {
            "is_anomaly": False,
            "score": score,
            "method": "auto:known_event_suppressed",
            "reason": (
                f"known_event={known_event} suppresses {metric_name} alert; "
                f"underlying robust_score={result['score']:.2f}, rel_change={rel_change:.2f}, "
                f"direction={direction}"
            ),
            "needs_review": bool(is_anomaly),
            "suppressed_score": score,
            "direction": direction,
        }

    return {
        "is_anomaly": is_anomaly,
        "score": score,
        "method": f"auto:{result['method']}+ewma",
        "reason": (
            f"metric={metric_name}, baseline={base_note}(n={base.size}), "
            f"robust_score={result['score']:.2f}, ewma={ewma:.2f}, "
            f"rel_change={rel_change:.2f}, direction={direction}"
        ),
    }
