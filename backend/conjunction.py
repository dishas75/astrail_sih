"""
ASTRAIL - conjunction (close-approach) detection and risk screening.

Features:
  1. Apogee-Perigee altitude shell sweep-and-prune filter (eliminates 80-90% of pairs).
  2. Coarse minimum search across lookahead window.
  3. Fine refinement (10-second steps) for accurate TCA, miss distance, and relative velocity.
  4. Full ConjunctionAlert generation with sub-satellite geodetic positions at TCA.
"""
import hashlib
import time
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Dict, List, Optional, Tuple

import numpy as np

from propagate import TrackedObject, teme_to_geodetic
from risk import score_event

_SCAN_CACHE: Dict[str, Tuple[List[dict], float]] = {}
SCAN_CACHE_TTL = 20.0  # seconds


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b))


def _coarse_minima(obj_a: TrackedObject, obj_b: TrackedObject, start: datetime,
                   hours: float, coarse_step_s: int):
    n_steps = int((hours * 3600) // coarse_step_s) + 1
    dists = []
    times = []
    for i in range(n_steps):
        t = start + timedelta(seconds=i * coarse_step_s)
        sa = obj_a.state_at(t)
        sb = obj_b.state_at(t)
        if sa is None or sb is None:
            dists.append(None)
        else:
            dists.append(_dist(sa[0], sb[0]))
        times.append(t)

    minima_idx = []
    for i in range(1, len(dists) - 1):
        if dists[i] is None:
            continue
        left = dists[i - 1] if dists[i - 1] is not None else float("inf")
        right = dists[i + 1] if dists[i + 1] is not None else float("inf")
        if dists[i] <= left and dists[i] <= right:
            minima_idx.append(i)
    if dists and dists[0] is not None and (len(dists) == 1 or dists[0] <= (dists[1] or float("inf"))):
        minima_idx.append(0)
    return times, dists, minima_idx


def _refine(obj_a: TrackedObject, obj_b: TrackedObject, center: datetime,
            bracket_s: int, fine_step_s: int = 10):
    best_t, best_d, best_rel_v = None, float("inf"), 0.0
    best_sa, best_sb = None, None
    steps = int((bracket_s * 2) // fine_step_s)
    t0 = center - timedelta(seconds=bracket_s)
    for i in range(steps + 1):
        t = t0 + timedelta(seconds=i * fine_step_s)
        sa = obj_a.state_at(t)
        sb = obj_b.state_at(t)
        if sa is None or sb is None:
            continue
        d = _dist(sa[0], sb[0])
        if d < best_d:
            best_d = d
            best_t = t
            best_rel_v = float(np.linalg.norm(sa[1] - sb[1]))
            best_sa, best_sb = sa, sb
    return best_t, best_d, best_rel_v, best_sa, best_sb


def find_conjunctions(objects: List[TrackedObject], start: datetime, hours: float,
                       threshold_km: float = 25.0, coarse_step_s: int = 300,
                       max_pairs: int = 4000, catalog_group: str = "active") -> List[dict]:
    # Check cache for identical requests within TTL
    key = f"{len(objects)}_{hours:.1f}_{threshold_km:.1f}_{start.strftime('%Y%m%d%H%M')}"
    now_ts = time.time()
    cached = _SCAN_CACHE.get(key)
    if cached and (now_ts - cached[1] < SCAN_CACHE_TTL):
        return cached[0]

    results = []
    pairs = list(combinations(objects, 2))

    # Stage 1: Apogee / Perigee altitude shell pre-filtering
    # If the altitude envelopes of two objects do not overlap within threshold_km, skip
    candidate_pairs = []
    for a, b in pairs:
        # Buffer with margin for safety
        margin = max(threshold_km * 2.0, 50.0)
        if (a.perigee_km - margin > b.apogee_km) or (b.perigee_km - margin > a.apogee_km):
            continue
        candidate_pairs.append((a, b))

    if len(candidate_pairs) > max_pairs:
        candidate_pairs = candidate_pairs[:max_pairs]

    by_norad = {o.norad_id: o for o in objects}

    for obj_a, obj_b in candidate_pairs:
        times, dists, minima_idx = _coarse_minima(obj_a, obj_b, start, hours, coarse_step_s)
        for idx in minima_idx:
            coarse_d = dists[idx]
            if coarse_d is None or coarse_d > threshold_km * 4:
                continue
            t_center = times[idx]
            refine_t, refine_d, rel_v, sa_tca, sb_tca = _refine(
                obj_a, obj_b, t_center, bracket_s=coarse_step_s, fine_step_s=10
            )
            if refine_t is None or refine_d > threshold_km:
                continue

            # Geodetic positions at TCA
            sat1_lat, sat1_lon, sat1_alt = teme_to_geodetic(sa_tca[0], refine_t)
            sat2_lat, sat2_lon, sat2_alt = teme_to_geodetic(sb_tca[0], refine_t)

            age_a = by_norad[obj_a.norad_id].epoch_age_days(start) if obj_a.norad_id in by_norad else 1.0
            age_b = by_norad[obj_b.norad_id].epoch_age_days(start) if obj_b.norad_id in by_norad else 1.0
            score_data = score_event(refine_d, rel_v, threshold_km, age_a, age_b)

            # Unique deterministic ID
            event_id = hashlib.md5(
                f"{min(obj_a.norad_id, obj_b.norad_id)}_{max(obj_a.norad_id, obj_b.norad_id)}_{refine_t.strftime('%Y%m%d%H%M')}".encode()
            ).hexdigest()[:12]

            event = {
                "id": event_id,
                "catalog_group": catalog_group or "active",
                "sat1_id": obj_a.norad_id,
                "sat1_name": obj_a.name,
                "sat2_id": obj_b.norad_id,
                "sat2_name": obj_b.name,
                "tca_utc": refine_t.isoformat(),
                "miss_distance_km": round(refine_d, 3),
                "relative_velocity_km_s": round(rel_v, 3),
                "risk_score": round(score_data["risk_score"] / 100.0, 4),  # normalized 0.0 - 1.0 for OpenAPI
                "risk_level": score_data["risk_level"],
                "sat1_lat_at_tca": sat1_lat,
                "sat1_lon_at_tca": sat1_lon,
                "sat1_alt_at_tca_km": sat1_alt,
                "sat2_lat_at_tca": sat2_lat,
                "sat2_lon_at_tca": sat2_lon,
                "sat2_alt_at_tca_km": sat2_alt,
                # Backward-compatibility fields for existing frontend
                "object_a": {"name": obj_a.name, "norad_id": obj_a.norad_id,
                             "object_type": obj_a.object_type, "group": obj_a.group},
                "object_b": {"name": obj_b.name, "norad_id": obj_b.norad_id,
                             "object_type": obj_b.object_type, "group": obj_b.group},
                "tca": refine_t.isoformat(),
                "hours_to_tca": round((refine_t - start).total_seconds() / 3600, 2),
                "proximity_component": score_data["proximity_component"],
                "velocity_component": score_data["velocity_component"],
                "confidence_penalty": score_data["confidence_penalty"],
            }
            results.append(event)

    results.sort(key=lambda r: r["miss_distance_km"])
    _SCAN_CACHE[key] = (results, now_ts)
    return results
