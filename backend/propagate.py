"""
ASTRAIL - orbital propagation using SGP4.

Positions/velocities are computed in the TEME (True Equator, Mean Equinox)
frame, which is what SGP4 natively outputs. Distances between two objects
at the same instant are frame-independent, so TEME is sufficient for
conjunction (close-approach) detection without extra frame conversion.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
from sgp4.api import Satrec, jday


class TrackedObject:
    __slots__ = ("name", "norad_id", "sat", "valid", "group", "object_type")

    def __init__(self, name: str, norad_id, line1: str, line2: str,
                 group: str = "unknown", object_type: str = "PAYLOAD"):
        self.name = name
        self.norad_id = norad_id
        self.group = group
        self.object_type = object_type
        try:
            self.sat = Satrec.twoline2rv(line1, line2)
            self.valid = True
        except Exception:
            self.sat = None
            self.valid = False

    def state_at(self, dt: datetime):
        """Returns (position_km[3], velocity_km_s[3]) or None on error."""
        if not self.valid:
            return None
        jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute,
                      dt.second + dt.microsecond / 1e6)
        e, r, v = self.sat.sgp4(jd, fr)
        if e != 0:
            return None
        return np.array(r), np.array(v)

    def epoch_age_days(self, now: datetime) -> float:
        if not self.valid:
            return 999.0
        epoch_year = self.sat.epochyr
        full_year = 2000 + epoch_year if epoch_year < 57 else 1900 + epoch_year
        epoch_dt = datetime(full_year, 1, 1, tzinfo=timezone.utc) + timedelta(
            days=self.sat.epochdays - 1)
        return max(0.0, (now - epoch_dt).total_seconds() / 86400.0)


def build_tracked_objects(catalog: list[dict]) -> list[TrackedObject]:
    objs = []
    for entry in catalog:
        obj = TrackedObject(entry["name"], entry.get("norad_id"),
                             entry["line1"], entry["line2"],
                             group=entry.get("group", "unknown"),
                             object_type=entry.get("object_type", "PAYLOAD"))
        if obj.valid:
            objs.append(obj)
    return objs


def sample_track(obj: TrackedObject, start: datetime, hours: float, step_s: int = 120):
    """Returns list of (datetime, position_km) samples for plotting an orbit path."""
    n_steps = int((hours * 3600) // step_s)
    out = []
    for i in range(n_steps):
        t = start + timedelta(seconds=i * step_s)
        st = obj.state_at(t)
        if st is None:
            continue
        out.append((t, st[0]))
    return out
