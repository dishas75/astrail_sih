"""
ASTRAIL - conjunction (close-approach) detection.

Two-pass search per object pair:
  1. Coarse scan across the full window (default step 5 min) to find local
     minima of separation distance.
  2. Fine refinement (10 s step) in a small bracket around each coarse
     minimum to nail down the true closest-approach time/distance.

This keeps the pairwise search fast (O(pairs * coarse_steps)) while still
recovering close approaches that last only minutes.
"""
from datetime import datetime, timedelta
from itertools import combinations

import numpy as np

from propagate import TrackedObject


def _dist(a, b) -> float:
    return float(np.linalg.norm(a - b))


def _coarse_minima(obj_a, obj_b, start: datetime, hours: float, coarse_step_s: int):
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


def _refine(obj_a, obj_b, center: datetime, bracket_s: int, fine_step_s: int = 10):
    best_t, best_d, best_rel_v = None, float("inf"), 0.0
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
    return best_t, best_d, best_rel_v


def find_conjunctions(objects: list[TrackedObject], start: datetime, hours: float,
                       threshold_km: float = 25.0, coarse_step_s: int = 300,
                       max_pairs: int = 4000):
    results = []
    pairs = list(combinations(objects, 2))
    if len(pairs) > max_pairs:
        pairs = pairs[:max_pairs]

    for obj_a, obj_b in pairs:
        times, dists, minima_idx = _coarse_minima(obj_a, obj_b, start, hours, coarse_step_s)
        for idx in minima_idx:
            coarse_d = dists[idx]
            if coarse_d is None or coarse_d > threshold_km * 4:
                continue
            t_center = times[idx]
            refine_t, refine_d, rel_v = _refine(obj_a, obj_b, t_center,
                                                 bracket_s=coarse_step_s)
            if refine_t is None:
                continue
            if refine_d <= threshold_km:
                results.append({
                    "object_a": {"name": obj_a.name, "norad_id": obj_a.norad_id,
                                 "object_type": obj_a.object_type, "group": obj_a.group},
                    "object_b": {"name": obj_b.name, "norad_id": obj_b.norad_id,
                                 "object_type": obj_b.object_type, "group": obj_b.group},
                    "tca": refine_t.isoformat(),
                    "miss_distance_km": round(refine_d, 3),
                    "relative_velocity_km_s": round(rel_v, 3),
                    "hours_to_tca": round((refine_t - start).total_seconds() / 3600, 2),
                })
    results.sort(key=lambda r: r["miss_distance_km"])
    return results
