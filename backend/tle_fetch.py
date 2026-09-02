"""
ASTRAIL - TLE data ingestion.

Pulls live TLE sets from CelesTrak (free, no API key). Falls back to a
synthetic demo catalog if CelesTrak is unreachable, so the dashboard
never shows a blank screen (useful at hackathon venues with flaky wifi).
"""
import json
import time
from pathlib import Path

import requests

from tle_synth import generate_demo_catalog

CACHE_DIR = Path(__file__).parent / "_cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL_SECONDS = 60 * 30  # 30 min

CELESTRAK_BASE = "https://celestrak.org/NORAD/elements/gp.php"

GROUP_LABELS = {
    "stations": "Space Stations",
    "last-30-days": "Recently Launched",
    "cosmos-1408-debris": "Cosmos 1408 Debris",
    "iridium-33-debris": "Iridium 33 Debris",
    "cosmos-2251-debris": "Cosmos 2251 Debris",
    "active": "Active Satellites (sampled)",
}
GROUPS = GROUP_LABELS  # backwards-compatible alias


def classify_object(name: str) -> str:
    """Heuristic object classification from its catalog name, used to
    build the composition breakdowns in the Analytics tab. CelesTrak names
    follow a fairly consistent convention (e.g. 'COSMOS 1408 DEB',
    'FALCON 9 R/B') so this is reliable without extra metadata lookups."""
    n = name.upper()
    if "DEB" in n:
        return "DEBRIS"
    if "R/B" in n or "ROCKET BODY" in n or "ROCKETBODY" in n:
        return "ROCKET BODY"
    return "PAYLOAD"


def _parse_tle_text(text: str):
    lines = [l.rstrip("\n") for l in text.splitlines() if l.strip()]
    objects = []
    i = 0
    while i + 2 < len(lines) + 1 and i + 1 < len(lines):
        if not lines[i].startswith("1 ") and i + 2 <= len(lines):
            name = lines[i].strip()
            if i + 2 < len(lines):
                l1, l2 = lines[i + 1], lines[i + 2]
                if l1.startswith("1 ") and l2.startswith("2 "):
                    try:
                        norad_id = int(l1[2:7])
                    except ValueError:
                        norad_id = None
                    objects.append({
                        "name": name, "norad_id": norad_id,
                        "line1": l1, "line2": l2,
                        "object_type": classify_object(name),
                    })
            i += 3
        else:
            i += 1
    return objects


def _cache_path(group: str) -> Path:
    return CACHE_DIR / f"{group}.json"


def fetch_group(group: str, force_refresh: bool = False):
    cache_file = _cache_path(group)
    if not force_refresh and cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            return json.loads(cache_file.read_text()), "cache"

    try:
        resp = requests.get(
            CELESTRAK_BASE,
            params={"GROUP": group, "FORMAT": "tle"},
            timeout=8,
        )
        resp.raise_for_status()
        objects = _parse_tle_text(resp.text)
        if not objects:
            raise ValueError("empty TLE response")
        for o in objects:
            o["group"] = group
        cache_file.write_text(json.dumps(objects))
        return objects, "live"
    except Exception:
        if cache_file.exists():
            return json.loads(cache_file.read_text()), "stale-cache"
        return None, "unavailable"


def load_catalog(groups: list[str], demo_mode: bool = False, limit: int = 80):
    """Returns (objects, source_label). source_label is one of
    'live', 'cache', 'stale-cache', 'demo'."""
    if demo_mode:
        catalog = generate_demo_catalog()
        for o in catalog:
            o.setdefault("object_type", classify_object(o["name"]))
            o.setdefault("group", "demo-" + o["name"].split(" ")[0].replace("ASTRAIL-", "").lower())
        return catalog, "demo"

    combined = []
    sources = set()
    for g in groups:
        objs, src = fetch_group(g)
        if objs:
            combined.extend(objs)
            sources.add(src)

    if not combined:
        catalog = generate_demo_catalog()
        for o in catalog:
            o.setdefault("object_type", classify_object(o["name"]))
            o.setdefault("group", "demo-" + o["name"].split(" ")[0].replace("ASTRAIL-", "").lower())
        return catalog, "demo"

    # de-dupe by norad id, cap for performance
    seen = set()
    deduped = []
    for o in combined:
        key = o.get("norad_id")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(o)
        if len(deduped) >= limit:
            break

    label = "live" if "live" in sources else ("cache" if "cache" in sources else "stale-cache")
    return deduped, label
