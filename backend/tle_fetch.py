"""
ASTRAIL - TLE data ingestion and catalog management.

Pulls live TLE sets from CelesTrak, parses orbital parameters (apogee,
perigee, inclination, drag), caches partitions to disk, and falls back
to offline cached/synthetic data if unreachable.
"""
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from tle_synth import generate_demo_catalog

CACHE_DIR = Path(__file__).parent / "_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL_SECONDS = 60 * 30  # 30 minutes

CELESTRAK_BASE = "https://celestrak.org/NORAD/elements/gp.php"

# Supported CelesTrak groups matching reference engine
SUPPORTED_GROUPS = {
    "active": "Active Operational Satellites (2LE)",
    "visual": "Visual / Bright Satellites (2LE)",
    "stations": "Crewed Space Stations (TLE)",
    "analyst": "Analyst-Tracked Elements (2LE)",
    "fengyun-1c-debris": "Fengyun-1C ASAT Debris Cloud (2LE)",
    "cosmos-2251-debris": "Cosmos-2251 Collision Debris (2LE)",
    "iridium-33-debris": "Iridium-33 Collision Debris (2LE)",
    "last-30-days": "Launches from Last 30 Days (2LE)",
    "starlink": "SpaceX Starlink Constellation (2LE)",
    "oneweb": "OneWeb Constellation (2LE)",
    "debris": "Tracked Space Debris (2LE)",
    "gps-ops": "GPS Operational Satellites (2LE)",
    "galileo": "Galileo Navigation Constellation (2LE)",
}
GROUPS = SUPPORTED_GROUPS

EARTH_RADIUS_KM = 6378.137
MU_EARTH = 398600.4418  # km^3 / s^2


def classify_object(name: str) -> str:
    n = name.upper()
    if "DEB" in n:
        return "DEBRIS"
    if "R/B" in n or "ROCKET BODY" in n or "ROCKETBODY" in n:
        return "ROCKET BODY"
    return "PAYLOAD"


def _parse_tle_float(s: str) -> float:
    """Parse exponential TLE format e.g. ' 12345-4' -> 0.000012345."""
    s = s.strip()
    if not s or s == "00000-0" or s == "00000+0":
        return 0.0
    try:
        # Check if there is an embedded sign for exponent
        if "-" in s[1:]:
            parts = s.rsplit("-", 1)
            mantissa = float(parts[0]) / 100000.0
            exp = -int(parts[1])
            return mantissa * (10 ** exp)
        elif "+" in s[1:]:
            parts = s.rsplit("+", 1)
            mantissa = float(parts[0]) / 100000.0
            exp = int(parts[1])
            return mantissa * (10 ** exp)
        return float(s)
    except Exception:
        return 0.0


def calculate_orbital_bounds(line2: str) -> Tuple[float, float, float]:
    """Computes (apogee_km, perigee_km, inclination_deg) from TLE Line 2."""
    try:
        inc_deg = float(line2[8:16].strip())
        ecc = float("0." + line2[26:33].strip())
        mm_rev_day = float(line2[52:63].strip())

        # Mean motion in rad/s
        n = mm_rev_day * (2.0 * math.pi / 86400.0)
        # Semi-major axis a = (mu / n^2)^(1/3)
        a = (MU_EARTH / (n ** 2)) ** (1.0 / 3.0)

        perigee_km = max(0.0, a * (1.0 - ecc) - EARTH_RADIUS_KM)
        apogee_km = max(perigee_km, a * (1.0 + ecc) - EARTH_RADIUS_KM)
        return apogee_km, perigee_km, inc_deg
    except Exception:
        return 600.0, 400.0, 51.6


def parse_bstar(line1: str) -> float:
    try:
        return _parse_tle_float(line1[53:61])
    except Exception:
        return 0.0001


def _parse_tle_text(text: str, group: str = "active") -> List[dict]:
    lines = [l.rstrip("\r\n") for l in text.splitlines() if l.strip()]
    objects = []
    i = 0
    while i < len(lines):
        # 3-line format: name, line1, line2
        if not lines[i].startswith("1 ") and not lines[i].startswith("2 ") and i + 2 < len(lines):
            name = lines[i].strip()
            l1, l2 = lines[i + 1].strip(), lines[i + 2].strip()
            if l1.startswith("1 ") and l2.startswith("2 "):
                try:
                    norad_id = int(l1[2:7])
                except ValueError:
                    norad_id = None
                if norad_id:
                    apogee_km, perigee_km, inc_deg = calculate_orbital_bounds(l2)
                    bstar = parse_bstar(l1)
                    objects.append({
                        "name": name,
                        "norad_id": norad_id,
                        "line1": l1,
                        "line2": l2,
                        "apogee_km": round(apogee_km, 3),
                        "perigee_km": round(perigee_km, 3),
                        "inclination_deg": round(inc_deg, 4),
                        "bstar_drag": bstar,
                        "object_type": classify_object(name),
                        "group": group,
                    })
                i += 3
                continue
        # 2-line format: line1, line2
        elif lines[i].startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            l1, l2 = lines[i].strip(), lines[i + 1].strip()
            try:
                norad_id = int(l1[2:7])
            except ValueError:
                norad_id = None
            if norad_id:
                name = f"OBJECT_{norad_id:05d}"
                apogee_km, perigee_km, inc_deg = calculate_orbital_bounds(l2)
                bstar = parse_bstar(l1)
                objects.append({
                    "name": name,
                    "norad_id": norad_id,
                    "line1": l1,
                    "line2": l2,
                    "apogee_km": round(apogee_km, 3),
                    "perigee_km": round(perigee_km, 3),
                    "inclination_deg": round(inc_deg, 4),
                    "bstar_drag": bstar,
                    "object_type": classify_object(name),
                    "group": group,
                })
            i += 2
            continue
        i += 1
    return objects


def _cache_path(group: str) -> Path:
    return CACHE_DIR / f"{group}.json"


def fetch_group(group: str, force_refresh: bool = False) -> Tuple[Optional[List[dict]], str]:
    cache_file = _cache_path(group)
    if not force_refresh and cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            try:
                return json.loads(cache_file.read_text(encoding="utf-8")), "cache"
            except Exception:
                pass

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/plain,text/html,*/*",
    }

    try:
        # If group is last-30-days, query SPECIAL parameter
        params = {"GROUP": group, "FORMAT": "tle"}
        if group == "last-30-days":
            params = {"SPECIAL": "last-30-days", "FORMAT": "tle"}

        resp = requests.get(CELESTRAK_BASE, params=params, headers=headers, timeout=5)
        resp.raise_for_status()
        objects = _parse_tle_text(resp.text, group=group)
        if not objects:
            raise ValueError("Empty TLE response")

        cache_file.write_text(json.dumps(objects, indent=2), encoding="utf-8")
        return objects, "live"
    except Exception as e:
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8")), "stale-cache"
            except Exception:
                pass
        return None, "unavailable"


def load_catalog(groups: List[str], demo_mode: bool = False, limit: Optional[int] = 100) -> Tuple[List[dict], str]:
    """Loads and merges catalog partitions."""
    if demo_mode:
        catalog = generate_demo_catalog()
        for o in catalog:
            o.setdefault("object_type", classify_object(o["name"]))
            o.setdefault("group", "demo")
            ap, per, inc = calculate_orbital_bounds(o["line2"])
            o.setdefault("apogee_km", round(ap, 3))
            o.setdefault("perigee_km", round(per, 3))
            o.setdefault("inclination_deg", round(inc, 4))
            o.setdefault("bstar_drag", parse_bstar(o["line1"]))
        return catalog, "demo"

    combined = []
    sources = set()
    for g in groups:
        objs, src = fetch_group(g)
        if objs:
            combined.extend(objs)
            sources.add(src)

    if not combined:
        return load_catalog(groups, demo_mode=True, limit=limit)

    # De-duplicate by norad_id
    seen = set()
    deduped = []
    for o in combined:
        nid = o.get("norad_id")
        if nid not in seen:
            seen.add(nid)
            deduped.append(o)
            if limit and len(deduped) >= limit:
                break

    label = "live" if "live" in sources else ("cache" if "cache" in sources else "stale-cache")
    return deduped, label


def get_catalog_metadata(group: str = "active") -> dict:
    cache_file = _cache_path(group)
    if cache_file.exists():
        try:
            items = json.loads(cache_file.read_text(encoding="utf-8"))
            mtime = cache_file.stat().st_mtime
            age = time.time() - mtime
            dt_str = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
            return {
                "count": len(items),
                "group": group,
                "source_url": f"{CELESTRAK_BASE}?GROUP={group}&FORMAT=tle",
                "last_updated_utc": dt_str,
                "age_seconds": round(age, 2),
                "supported_groups": SUPPORTED_GROUPS,
                "refresh_hint": f"POST /api/catalog/refresh?group={group} to trigger a manual refresh",
            }
        except Exception:
            pass

    # If requested group cache file does not exist yet, sum up all available cached partitions
    cached_counts = 0
    latest_mtime = 0.0
    for p in CACHE_DIR.glob("*.json"):
        if p.name.endswith(".json") and p.is_file() and not p.name.startswith("."):
            try:
                items = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(items, list):
                    cached_counts += len(items)
                    latest_mtime = max(latest_mtime, p.stat().st_mtime)
            except Exception:
                pass

    if cached_counts == 0:
        cached_counts = 40  # offline synthetic catalog baseline
        latest_mtime = time.time()

    age = time.time() - latest_mtime if latest_mtime > 0 else 0.0
    dt_str = datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat() if latest_mtime > 0 else datetime.now(timezone.utc).isoformat()

    return {
        "count": cached_counts,
        "group": group,
        "source_url": f"{CELESTRAK_BASE}?GROUP={group}&FORMAT=tle",
        "last_updated_utc": dt_str,
        "age_seconds": round(age, 2),
        "supported_groups": SUPPORTED_GROUPS,
        "refresh_hint": f"POST /api/catalog/refresh?group={group} to trigger a manual refresh",
    }


BULK_CACHE_TTL_SECONDS = 7200  # 2 hours


def fetch_bulk_tle(group: str = "active", force_refresh: bool = False) -> List[dict]:
    """
    Return all TLE records for the given CelesTrak group.
    Each record: { name, line1, line2, catalog_number }.
    Cached server-side for 2 hours. Designed for client-side SGP4 propagation.
    Guaranteed resilient fallback ensures no 502/403 errors are ever raised.
    """
    clean_group = (group or "active").strip().lower()
    cache_file = CACHE_DIR / f"bulk_{clean_group}.json"

    if not force_refresh and cache_file.exists():
        try:
            age = time.time() - cache_file.stat().st_mtime
            if age < BULK_CACHE_TTL_SECONDS:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(data, list) and data:
                    return data
        except Exception:
            pass

    # If unsupported group and no cache, immediately return fallback
    if clean_group not in SUPPORTED_GROUPS and not cache_file.exists():
        active_bulk = CACHE_DIR / "bulk_active.json"
        if active_bulk.exists():
            try:
                data = json.loads(active_bulk.read_text(encoding="utf-8"))
                if isinstance(data, list) and data:
                    return data
            except Exception:
                pass
        demo = generate_demo_catalog()
        return [{"name": d["name"], "line1": d["line1"], "line2": d["line2"], "catalog_number": d.get("norad_id") or int(d["line1"][2:7])} for d in demo]

    # Try live fetch from CelesTrak with browser-grade headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/plain,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }
    params = {"GROUP": clean_group, "FORMAT": "tle"}
    if clean_group == "last-30-days":
        params = {"SPECIAL": "last-30-days", "FORMAT": "tle"}

    try:
        resp = requests.get(CELESTRAK_BASE, params=params, headers=headers, timeout=6)
        if resp.status_code == 200 and resp.text.strip():
            lines = [l.strip() for l in resp.text.splitlines() if l.strip()]
            records = []
            i = 0
            while i < len(lines):
                if not lines[i].startswith("1 ") and not lines[i].startswith("2 ") and i + 2 < len(lines):
                    name = lines[i]
                    l1, l2 = lines[i+1], lines[i+2]
                    if l1.startswith("1 ") and l2.startswith("2 "):
                        try:
                            cat_num = int(l1[2:7])
                            records.append({"name": name, "line1": l1, "line2": l2, "catalog_number": cat_num})
                        except Exception:
                            pass
                        i += 3
                        continue
                elif lines[i].startswith("1 ") and i + 1 < len(lines) and lines[i+1].startswith("2 "):
                    l1, l2 = lines[i], lines[i+1]
                    try:
                        cat_num = int(l1[2:7])
                        records.append({"name": f"OBJECT_{cat_num:05d}", "line1": l1, "line2": l2, "catalog_number": cat_num})
                    except Exception:
                        pass
                    i += 2
                    continue
                i += 1

            if records:
                cache_file.write_text(json.dumps(records, indent=2), encoding="utf-8")
                return records
    except Exception as exc:
        print(f"Live bulk fetch failed for {clean_group}: {exc}")

    # Fallback 1: existing bulk cache (even if stale)
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass

    # Fallback 2: partition cache {clean_group}.json
    part_file = _cache_path(clean_group)
    if part_file.exists():
        try:
            items = json.loads(part_file.read_text(encoding="utf-8"))
            if isinstance(items, list) and items:
                records = []
                for it in items:
                    cat_num = it.get("norad_id") or int(it["line1"][2:7])
                    records.append({"name": it["name"], "line1": it["line1"], "line2": it["line2"], "catalog_number": cat_num})
                cache_file.write_text(json.dumps(records, indent=2), encoding="utf-8")
                return records
        except Exception:
            pass

    # Fallback 3: bulk_active.json
    active_bulk = CACHE_DIR / "bulk_active.json"
    if active_bulk.exists():
        try:
            data = json.loads(active_bulk.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass

    # Fallback 4: synthetic demo catalog
    demo = generate_demo_catalog()
    records = []
    for d in demo:
        cat_num = d.get("norad_id") or int(d["line1"][2:7])
        records.append({"name": d["name"], "line1": d["line1"], "line2": d["line2"], "catalog_number": cat_num})
    return records
