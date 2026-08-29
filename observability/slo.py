from __future__ import annotations

from typing import Any, Iterable, Mapping


def calculate_slo(target: float, bad_events: int, total_events: int) -> dict[str, Any]:
    if not 0 < target < 1:
        raise ValueError("target must be between 0 and 1 (exclusive)")
    if bad_events < 0 or total_events < 0 or bad_events > total_events:
        raise ValueError("invalid event counts")
    allowed_bad_rate = 1.0 - target
    if total_events == 0:
        return {
            "target": target,
            "actual_bad_rate": 0.0,
            "allowed_bad_rate": allowed_bad_rate,
            "burn_rate": 0.0,
            "remaining_error_budget_fraction": 1.0,
            "breached": False,
        }
    actual_bad_rate = bad_events / total_events
    burn_rate = actual_bad_rate / allowed_bad_rate
    consumed_fraction = min(1.0, actual_bad_rate / allowed_bad_rate)
    return {
        "target": target,
        "actual_bad_rate": actual_bad_rate,
        "allowed_bad_rate": allowed_bad_rate,
        "burn_rate": burn_rate,
        "remaining_error_budget_fraction": max(0.0, 1.0 - consumed_fraction),
        "breached": bool(actual_bad_rate > allowed_bad_rate),
    }


def evaluate_multiwindow_burn(
    *,
    short_window_burn: float,
    long_window_burn: float,
    fast_burn: float = 14.4,
    slow_burn: float = 6.0,
) -> dict[str, Any]:
    """Multi-window, multi-burn-rate alerting (Google SRE workbook).

    Both windows must be burning for an alert. This is what separates a
    *sustained* problem from a *transient* spike:

    - short window high, long window high  -> real, ongoing burn  -> page / ticket
    - short window high, long window low    -> transient spike     -> no alert
    - short window low                      -> already recovering  -> no alert

    Thresholds default to the classic 2%-budget-in-1h (``14.4``) fast burn and
    5%-budget-in-6h (``6``) slow burn.
    """
    both = min(short_window_burn, long_window_burn)

    if both >= fast_burn:
        page, severity, kind = True, "critical", "sustained_fast_burn"
    elif both >= slow_burn:
        page, severity, kind = True, "warning", "sustained_slow_burn"
    elif short_window_burn >= fast_burn and long_window_burn < slow_burn:
        page, severity, kind = False, "info", "transient_spike"
    elif short_window_burn < 1.0:
        page, severity, kind = False, "info", "recovering"
    else:
        page, severity, kind = False, "info", "within_budget"

    return {
        "page": page,
        "severity": severity,
        "reason": (
            f"{kind}: short={short_window_burn:.2f}, long={long_window_burn:.2f}, "
            f"fast_threshold={fast_burn}, slow_threshold={slow_burn}"
        ),
        "kind": kind,
        "short_window_burn": short_window_burn,
        "long_window_burn": long_window_burn,
    }


# --------------------------------------------------------------------------- #
# Bonus: full multi-tier, multi-window burn-rate policy (SRE workbook table).
# --------------------------------------------------------------------------- #
# Each tier fires only when BOTH its long and short windows are burning above
# ``factor``. Long window = how much budget the tier is willing to spend before
# alerting; short window = confirmation that the burn is still happening now.
DEFAULT_BURN_TIERS: tuple[dict[str, Any], ...] = (
    {"name": "2pct_1h", "factor": 14.4, "long_window": "1h", "short_window": "5m", "action": "page", "severity": "critical"},
    {"name": "5pct_6h", "factor": 6.0, "long_window": "6h", "short_window": "30m", "action": "page", "severity": "critical"},
    {"name": "10pct_3d", "factor": 1.0, "long_window": "3d", "short_window": "6h", "action": "ticket", "severity": "warning"},
)


def evaluate_burn_policy(
    window_burn_rates: Mapping[str, float],
    *,
    tiers: Iterable[Mapping[str, Any]] = DEFAULT_BURN_TIERS,
) -> dict[str, Any]:
    """Evaluate a multi-tier burn-rate policy.

    ``window_burn_rates`` maps a window label (e.g. ``"1h"``, ``"5m"``, ``"6h"``,
    ``"30m"``, ``"3d"``) to its current burn rate (actual_bad_rate / allowed).

    Returns the most severe firing tier (page beats ticket), or a quiet result.
    """
    firing = []
    for tier in tiers:
        long_burn = window_burn_rates.get(tier["long_window"])
        short_burn = window_burn_rates.get(tier["short_window"])
        if long_burn is None or short_burn is None:
            continue
        if long_burn >= tier["factor"] and short_burn >= tier["factor"]:
            firing.append(
                {
                    "tier": tier["name"],
                    "action": tier["action"],
                    "severity": tier["severity"],
                    "long_window": tier["long_window"],
                    "long_burn": long_burn,
                    "short_window": tier["short_window"],
                    "short_burn": short_burn,
                    "factor": tier["factor"],
                }
            )

    if not firing:
        return {
            "page": False,
            "ticket": False,
            "severity": "info",
            "reason": "no burn tier firing",
            "firing_tiers": [],
        }

    rank = {"page": 2, "ticket": 1}
    firing.sort(key=lambda f: rank.get(f["action"], 0), reverse=True)
    top = firing[0]
    return {
        "page": any(f["action"] == "page" for f in firing),
        "ticket": any(f["action"] == "ticket" for f in firing),
        "severity": top["severity"],
        "reason": (
            f"tier {top['tier']} firing: {top['long_window']} burn={top['long_burn']:.2f} "
            f"and {top['short_window']} burn={top['short_burn']:.2f} >= {top['factor']}"
        ),
        "firing_tiers": firing,
    }
