from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from observability.anomaly import mad_detector, zscore_detector


def approximate_token_lengths(texts: Iterable[str]) -> list[int]:
    # Deliberately simple proxy; no tokenizer/model download needed.
    return [len(str(t).split()) for t in texts]


def detect_text_length_shift(
    current_texts: Iterable[str],
    baseline_batch_means: Iterable[float],
    *,
    threshold: float = 3.0,
) -> dict[str, Any]:
    """Retrieval-content length drift.

    A collapse in answer/chunk length is a classic RAG regression (truncated
    context, empty retrieval, a broken chunker). We compare the current batch's
    mean token length against the baseline per-batch means with a robust
    (median/MAD) score, and also flag a large relative move so an obvious
    collapse trips even when the baseline spread is tiny.
    """
    lengths = approximate_token_lengths(current_texts)
    current_mean = float(np.mean(lengths)) if lengths else 0.0
    base = list(baseline_batch_means)

    robust = mad_detector(current_mean, base, threshold=threshold + 0.5)
    z = zscore_detector(current_mean, base, threshold=threshold)

    base_median = float(np.median(base)) if base else 0.0
    rel_change = abs(current_mean - base_median) / (abs(base_median) + 1e-9)

    is_anomaly = bool(robust["is_anomaly"] or z["is_anomaly"] or rel_change >= 0.4)
    return {
        "is_anomaly": is_anomaly,
        "score": float(max(robust["score"], z["score"], rel_change)),
        "method": f"text_length:{robust['method']}+zscore",
        "reason": (
            f"current_mean={current_mean:.2f}, baseline_median={base_median:.2f}, "
            f"rel_change={rel_change:.3f}, robust_score={robust['score']:.2f}, "
            f"z={z['score']:.2f}"
        ),
        "metric": "mean_text_length",
        "current_mean": current_mean,
    }


def detect_embedding_norm_shift(
    current_norms: Iterable[float],
    baseline_norms: Iterable[float],
    *,
    threshold: float = 3.5,
) -> dict[str, Any]:
    """Embedding-space drift proxy using the L2-norm distribution.

    A healthy index has embedding norms that sit in a stable band. A model swap,
    a truncated/empty-content batch, or a broken embedding step moves the mean
    norm. We compare the current batch's mean norm against the baseline batch
    norms with a robust (median/MAD) score, and also flag a large relative move.
    """
    cur = np.asarray(list(current_norms), dtype=float)
    base = np.asarray(list(baseline_norms), dtype=float)
    if cur.size == 0 or base.size == 0:
        return {"is_anomaly": False, "score": 0.0, "method": "embedding_norm", "reason": "empty_input"}

    current_mean = float(np.mean(cur))
    result = mad_detector(current_mean, base, threshold=threshold)

    base_median = float(np.median(base))
    rel_change = abs(current_mean - base_median) / (abs(base_median) + 1e-9)

    is_anomaly = bool(result["is_anomaly"] or rel_change >= 0.15)
    return {
        "is_anomaly": is_anomaly,
        "score": float(max(result["score"], rel_change)),
        "method": f"embedding_norm:{result['method']}",
        "reason": (
            f"current_mean_norm={current_mean:.4f}, baseline_median={base_median:.4f}, "
            f"rel_change={rel_change:.3f}, robust_score={result['score']:.2f}"
        ),
        "current_mean_norm": current_mean,
    }
