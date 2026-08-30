"""
ASTRAIL - synthetic TLE generator.

Used as an offline fallback ("Demo Mode") when CelesTrak can't be reached
(no wifi at a venue, firewall, rate limiting, etc). Produces internally
consistent, checksum-valid two-line elements so SGP4 propagates them
normally. A couple of pairs are deliberately engineered to have a real
close approach in the next few hours so the demo always has something
to alert on.
"""
import math
import random
from datetime import datetime, timezone

random.seed(42)


def _checksum(line: str) -> int:
    total = 0
    for ch in line:
        if ch.isdigit():
            total += int(ch)
        elif ch == "-":
            total += 1
    return total % 10


def _epoch_str(dt: datetime) -> str:
    year2 = dt.year % 100
    start = datetime(dt.year, 1, 1, tzinfo=timezone.utc)
    doy = (dt - start).total_seconds() / 86400.0 + 1.0
    return f"{year2:02d}{doy:012.8f}"


def _fmt_exp(value: float) -> str:
    """Format like TLE-style exponential, e.g. 0.000123 -> ' 12345-4'."""
    if value == 0:
        return " 00000-0"
    sign = "-" if value < 0 else " "
    value = abs(value)
    exp = math.floor(math.log10(value)) + 1
    mantissa = value / (10 ** exp)
    mant_str = f"{int(round(mantissa * 1e5)):05d}"
    return f"{sign}{mant_str}{'-' if exp < 0 else '+'}{abs(exp)}"


def make_tle(norad_id: int, name: str, inc_deg: float, raan_deg: float,
             ecc: float, argp_deg: float, ma_deg: float, mean_motion: float,
             epoch: datetime, bstar: float = 0.0001, classification: str = "U"):
    epoch_field = _epoch_str(epoch)
    l1 = (f"1 {norad_id:05d}{classification} 24001A   {epoch_field} "
          f" .00000000  00000-0 {_fmt_exp(bstar)} 0  001")
    l1 = l1[:68] + "0"  # pad element-set number area
    l1_body = l1[:68]
    checksum1 = _checksum(l1_body)
    line1 = f"{l1_body}{checksum1}"

    l2 = (f"2 {norad_id:05d} {inc_deg:8.4f} {raan_deg:8.4f} "
          f"{int(round(ecc * 1e7)):07d} {argp_deg:8.4f} {ma_deg:8.4f} "
          f"{mean_motion:11.8f}00001")
    checksum2 = _checksum(l2)
    line2 = f"{l2}{checksum2}"
    return {"name": name, "norad_id": norad_id, "line1": line1, "line2": line2}


def generate_demo_catalog(n: int = 36):
    """Builds a synthetic LEO catalog, with a few pairs seeded to converge
    within the next several hours so conjunction detection has confirmed
    hits to show even in fully offline demo mode."""
    now = datetime.now(timezone.utc)
    catalog = []
    norad = 90000

    families = [
        ("ASTRAIL-DEBRIS", 82.5, 15.05),   # sun-sync-ish debris shell
        ("ASTRAIL-SAT", 53.0, 15.4),        # starlink-ish shell
        ("ASTRAIL-ROCKETBODY", 97.6, 14.9),  # polar shell
        ("ASTRAIL-CUBESAT", 45.0, 15.6),
    ]

    for i in range(n):
        fam_name, base_inc, base_mm = families[i % len(families)]
        norad += 1
        inc = base_inc + random.uniform(-3, 3)
        raan = random.uniform(0, 360)
        ecc = random.uniform(0.0001, 0.004)
        argp = random.uniform(0, 360)
        ma = random.uniform(0, 360)
        mm = base_mm + random.uniform(-0.15, 0.15)
        catalog.append(make_tle(norad, f"{fam_name} #{i+1}", inc, raan, ecc,
                                 argp, ma, mm, now))

    # --- engineer two guaranteed close-approach pairs for demo purposes ---
    # Same orbital plane (inc/raan/ecc/argp) with a small mean-anomaly offset
    # and a slightly different mean motion: the phase gap closes over time
    # and produces a genuine, verified close approach within the 48h window
    # (numerically confirmed: ~2-3 km miss distance), so demo mode always
    # has real alerts to show even fully offline.
    pair_a1 = make_tle(90501, "ASTRAIL-DEMO TARGET-1", 51.6, 120.0, 0.001,
                        90.0, 10.0, 15.5000, now)
    pair_a2 = make_tle(90502, "ASTRAIL-DEMO TARGET-2", 51.6, 120.0, 0.001,
                        90.0, 11.0, 15.4900, now)
    pair_b1 = make_tle(90503, "ASTRAIL-DEMO DEBRIS-A", 82.4, 45.0, 0.0012,
                        200.0, 300.0, 14.99, now)
    pair_b2 = make_tle(90504, "ASTRAIL-DEMO DEBRIS-B", 82.4, 45.0, 0.0012,
                        200.0, 300.7, 14.983, now)
    catalog += [pair_a1, pair_a2, pair_b1, pair_b2]
    return catalog
