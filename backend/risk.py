"""
ASTRAIL - collision risk scoring.

Per-event risk score (0-100) blends three factors:
  - Proximity: closer miss distance -> higher risk (dominant factor)
  - Kinetic severity: higher relative velocity -> more destructive impact
  - Data confidence: older TLE epoch -> less certain state -> risk bump

The 'Kessler Index' is a single 0-100 dial summarizing catalog-wide risk
across all detected conjunctions in the current window - our headline
"mission control" number.
"""
import math


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def score_event(miss_distance_km: float, relative_velocity_km_s: float,
                 threshold_km: float, tle_age_days_a: float, tle_age_days_b: float) -> dict:
    proximity = _clamp(100 * (1 - min(miss_distance_km, threshold_km) / threshold_km))

    # Most LEO collisions happen in the ~3-15 km/s range; normalize against that.
    velocity_score = _clamp(100 * min(relative_velocity_km_s / 15.0, 1.0))

    avg_age = (tle_age_days_a + tle_age_days_b) / 2
    confidence_penalty = _clamp(100 * min(avg_age / 10.0, 1.0))

    risk = 0.62 * proximity + 0.28 * velocity_score + 0.10 * confidence_penalty
    risk = _clamp(risk)

    if risk >= 75:
        level = "CRITICAL"
    elif risk >= 50:
        level = "HIGH"
    elif risk >= 25:
        level = "MODERATE"
    else:
        level = "LOW"

    return {
        "risk_score": round(risk, 1),
        "risk_level": level,
        "proximity_component": round(proximity, 1),
        "velocity_component": round(velocity_score, 1),
        "confidence_penalty": round(confidence_penalty, 1),
    }


def kessler_index(scored_events: list[dict], total_objects: int) -> dict:
    """Aggregate 0-100 dial: combines how bad the worst events are with how
    many risky events are piling up relative to catalog size."""
    if not scored_events:
        return {"index": 0.0, "label": "NOMINAL", "critical_count": 0, "high_count": 0}

    scores = [e["risk_score"] for e in scored_events]
    top = sorted(scores, reverse=True)[:5]
    peak_component = sum(top) / len(top)

    critical_count = sum(1 for e in scored_events if e["risk_level"] == "CRITICAL")
    high_count = sum(1 for e in scored_events if e["risk_level"] == "HIGH")

    density = min(len(scored_events) / max(total_objects, 1), 1.0) * 100

    idx = _clamp(0.7 * peak_component + 0.3 * density)

    if idx >= 70:
        label = "SEVERE"
    elif idx >= 45:
        label = "ELEVATED"
    elif idx >= 20:
        label = "WATCH"
    else:
        label = "NOMINAL"

    return {
        "index": round(idx, 1),
        "label": label,
        "critical_count": critical_count,
        "high_count": high_count,
    }
