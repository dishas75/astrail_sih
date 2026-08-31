"""
ASTRAIL - orbital propagation using SGP4.

Positions and velocities are computed in the TEME (True Equator, Mean Equinox)
frame, with conversion to geodetic sub-satellite points (lat, lon, alt_km)
accounting for Earth's sidereal rotation.
"""
import math
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import numpy as np
from sgp4.api import Satrec, jday

from schemas import TrackPoint

EARTH_RADIUS_KM = 6378.137


def teme_to_geodetic(r_teme: np.ndarray, dt: datetime) -> Tuple[float, float, float]:
    """
    Converts TEME Cartesian position [x, y, z] km to geodetic (lat_deg, lon_deg, alt_km).
    Accounts for Greenwich Mean Sidereal Time (GMST) rotation.
    """
    x, y, z = float(r_teme[0]), float(r_teme[1]), float(r_teme[2])
    r = math.sqrt(x * x + y * y + z * z)
    if r == 0:
        return 0.0, 0.0, 0.0

    alt_km = r - EARTH_RADIUS_KM
    lat_rad = math.asin(max(-1.0, min(1.0, z / r)))
    lat_deg = math.degrees(lat_rad)

    # Right ascension in degrees
    ra_deg = math.degrees(math.atan2(y, x)) % 360.0

    # Julian date for GMST calculation
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + dt.microsecond / 1e6)
    full_jd = jd + fr
    d = full_jd - 2451545.0
    t = d / 36525.0
    # GMST in degrees
    gmst_deg = (280.46061837 + 360.98564736629 * d + 0.000387933 * (t ** 2) - (t ** 3) / 38710000.0) % 360.0

    # Longitude = Right Ascension - GMST
    lon_deg = (ra_deg - gmst_deg) % 360.0
    if lon_deg > 180.0:
        lon_deg -= 360.0

    return round(lat_deg, 5), round(lon_deg, 5), round(alt_km, 3)


class TrackedObject:
    __slots__ = ("name", "norad_id", "sat", "valid", "group", "object_type",
                 "apogee_km", "perigee_km", "inclination_deg", "bstar_drag", "line1", "line2")

    def __init__(self, name: str, norad_id: int, line1: str, line2: str,
                 group: str = "unknown", object_type: str = "PAYLOAD",
                 apogee_km: float = 600.0, perigee_km: float = 400.0,
                 inclination_deg: float = 51.6, bstar_drag: float = 0.0001):
        self.name = name
        self.norad_id = int(norad_id) if norad_id is not None else 0
        self.line1 = line1
        self.line2 = line2
        self.group = group
        self.object_type = object_type
        self.apogee_km = apogee_km
        self.perigee_km = perigee_km
        self.inclination_deg = inclination_deg
        self.bstar_drag = bstar_drag

        try:
            self.sat = Satrec.twoline2rv(line1, line2)
            self.valid = True
        except Exception:
            self.sat = None
            self.valid = False

    def state_at(self, dt: datetime) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """Returns (position_km[3], velocity_km_s[3]) in TEME or None on error."""
        if not self.valid or self.sat is None:
            return None
        jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + dt.microsecond / 1e6)
        e, r, v = self.sat.sgp4(jd, fr)
        if e != 0:
            return None
        return np.array(r, dtype=float), np.array(v, dtype=float)

    def epoch_age_days(self, now: datetime) -> float:
        if not self.valid or self.sat is None:
            return 999.0
        epoch_year = self.sat.epochyr
        full_year = 2000 + epoch_year if epoch_year < 57 else 1900 + epoch_year
        epoch_dt = datetime(full_year, 1, 1, tzinfo=timezone.utc) + timedelta(days=self.sat.epochdays - 1)
        return max(0.0, (now - epoch_dt).total_seconds() / 86400.0)


def build_tracked_objects(catalog: List[dict]) -> List[TrackedObject]:
    objs = []
    for entry in catalog:
        obj = TrackedObject(
            name=entry["name"],
            norad_id=entry.get("norad_id", 0),
            line1=entry["line1"],
            line2=entry["line2"],
            group=entry.get("group", "unknown"),
            object_type=entry.get("object_type", "PAYLOAD"),
            apogee_km=entry.get("apogee_km", 600.0),
            perigee_km=entry.get("perigee_km", 400.0),
            inclination_deg=entry.get("inclination_deg", 51.6),
            bstar_drag=entry.get("bstar_drag", 0.0001),
        )
        if obj.valid:
            objs.append(obj)
    return objs


def sample_ground_track(obj: TrackedObject, start: datetime, hours: float, step_minutes: float = 2.0) -> List[TrackPoint]:
    """Generates ground-track points (t, lat, lon, alt_km) for 2D/3D map renderers."""
    step_s = max(6.0, step_minutes * 60.0)
    n_steps = max(1, int((hours * 3600.0) // step_s))
    points: List[TrackPoint] = []

    for i in range(n_steps + 1):
        t = start + timedelta(seconds=i * step_s)
        st = obj.state_at(t)
        if st is None:
            continue
        lat, lon, alt = teme_to_geodetic(st[0], t)
        points.append(TrackPoint(
            t=t.isoformat(),
            lat=lat,
            lon=lon,
            alt_km=alt,
        ))

    return points


def sample_track(obj: TrackedObject, start: datetime, hours: float, step_s: int = 120):
    """Returns list of (datetime, position_km) in TEME frame for 3D viewer backward compatibility."""
    n_steps = max(1, int((hours * 3600) // step_s))
    out = []
    for i in range(n_steps + 1):
        t = start + timedelta(seconds=i * step_s)
        st = obj.state_at(t)
        if st is None:
            continue
        out.append((t, st[0]))
    return out
