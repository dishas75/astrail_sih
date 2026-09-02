import csv
import io
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from conjunction import find_conjunctions
from propagate import build_tracked_objects, sample_track
from risk import kessler_index, score_event
from tle_fetch import GROUPS, load_catalog

app = FastAPI(title="ASTRAIL API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_cache = {}  # keyed by (tuple(sorted(groups)), demo_mode) -> (objects, source, built_at)
CACHE_TTL = 300  # rebuild tracked objects every 5 min at most


def _refresh(groups, demo_mode):
    """Returns (objects, source) for this request WITHOUT mutating any
    shared state other request handlers are reading from concurrently."""
    key = (tuple(sorted(groups)), demo_mode)
    now = time.time()
    cached = _cache.get(key)
    if cached and (now - cached[2] < CACHE_TTL):
        return cached[0], cached[1]
    catalog, source = load_catalog(groups, demo_mode=demo_mode)
    objects = build_tracked_objects(catalog)
    _cache[key] = (objects, source, now)
    return objects, source


@app.get("/api/status")
def status():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/groups")
def groups():
    return GROUPS


@app.get("/api/catalog")
def catalog(groups_param: str = Query("stations,cosmos-1408-debris,iridium-33-debris,cosmos-2251-debris",
                                       alias="groups"),
            demo: bool = Query(False)):
    group_list = [g for g in groups_param.split(",") if g]
    objects, source = _refresh(group_list, demo)
    now = datetime.now(timezone.utc)
    items = []
    for obj in objects:
        st = obj.state_at(now)
        pos = st[0].tolist() if st else None
        items.append({
            "name": obj.name,
            "norad_id": obj.norad_id,
            "position_km": pos,
            "tle_age_days": round(obj.epoch_age_days(now), 2),
        })
    return {
        "source": source,
        "count": len(items),
        "objects": items,
    }


@app.get("/api/orbits")
def orbits(groups_param: str = Query("stations,cosmos-1408-debris,iridium-33-debris,cosmos-2251-debris",
                                      alias="groups"),
           demo: bool = Query(False),
           hours: float = Query(1.5, ge=0.1, le=6),
           step_s: int = Query(90, ge=10, le=600)):
    group_list = [g for g in groups_param.split(",") if g]
    objects, source = _refresh(group_list, demo)
    now = datetime.now(timezone.utc)
    tracks = []
    for obj in objects:
        pts = sample_track(obj, now, hours, step_s)
        tracks.append({
            "name": obj.name,
            "norad_id": obj.norad_id,
            "points": [p[1].tolist() for p in pts],
        })
    return {"source": source, "generated_at": now.isoformat(), "tracks": tracks}


@app.get("/api/conjunctions")
def conjunctions(groups_param: str = Query("stations,cosmos-1408-debris,iridium-33-debris,cosmos-2251-debris",
                                            alias="groups"),
                  demo: bool = Query(False),
                  hours: float = Query(48, ge=1, le=168),
                  threshold_km: float = Query(25.0, ge=1, le=200)):
    group_list = [g for g in groups_param.split(",") if g]
    objects, source = _refresh(group_list, demo)
    now = datetime.now(timezone.utc)

    raw_events = find_conjunctions(objects, now, hours, threshold_km=threshold_km)

    by_norad = {o.norad_id: o for o in objects}
    scored = []
    for ev in raw_events:
        age_a = by_norad[ev["object_a"]["norad_id"]].epoch_age_days(now)
        age_b = by_norad[ev["object_b"]["norad_id"]].epoch_age_days(now)
        score = score_event(ev["miss_distance_km"], ev["relative_velocity_km_s"],
                             threshold_km, age_a, age_b)
        scored.append({**ev, **score})

    kessler = kessler_index(scored, total_objects=len(objects))

    return {
        "source": source,
        "generated_at": now.isoformat(),
        "window_hours": hours,
        "threshold_km": threshold_km,
        "object_count": len(objects),
        "kessler_index": kessler,
        "events": scored,
    }


@app.get("/api/export/alerts.csv")
def export_alerts_csv(groups_param: str = Query("stations,cosmos-1408-debris,iridium-33-debris,cosmos-2251-debris",
                                                  alias="groups"),
                       demo: bool = Query(False),
                       hours: float = Query(48, ge=1, le=168),
                       threshold_km: float = Query(25.0, ge=1, le=200)):
    data = conjunctions(groups_param, demo, hours, threshold_km)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Object A", "NORAD A", "Object B", "NORAD B", "TCA (UTC)",
                      "Miss Distance (km)", "Relative Velocity (km/s)",
                      "Risk Score", "Risk Level"])
    for e in data["events"]:
        writer.writerow([
            e["object_a"]["name"], e["object_a"]["norad_id"],
            e["object_b"]["name"], e["object_b"]["norad_id"],
            e["tca"], e["miss_distance_km"], e["relative_velocity_km_s"],
            e["risk_score"], e["risk_level"],
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=astrail_conjunction_report.csv"},
    )


# Serve the frontend last so /api/* routes above take precedence.
app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")