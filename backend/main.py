"""
Space Debris Tracking & Collision Risk Engine.
Astrodynamics SGP4 propagation and conjunction assessment API.
"""
import csv
import io
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Path as FPath, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from conjunction import find_conjunctions
from propagate import (
    TrackedObject,
    build_tracked_objects,
    sample_ground_track,
    sample_track,
)
from risk import kessler_index, score_event
from schemas import (
    ConjunctionAlert,
    OrbitTracksResponse,
    RecentlyViewedSatellite,
    RecordViewRequest,
    SatelliteOrbitTrack,
    SatelliteRecord,
    SaveSatelliteRequest,
    SavedSatellite,
    TleBulkRecord,
    TrackPoint,
)
from storage import storage
from tle_fetch import (
    GROUPS,
    SUPPORTED_GROUPS,
    fetch_bulk_tle,
    fetch_group,
    get_catalog_metadata,
    load_catalog,
)

app = FastAPI(
    title="Space Debris Tracking & Collision Risk Engine",
    description="Astrodynamics SGP4 propagation and conjunction assessment API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_CACHE: Dict[tuple, tuple] = {}
CACHE_TTL = 300  # seconds


def _refresh(groups: List[str], demo_mode: bool = False, limit: Optional[int] = 120):
    key = (tuple(sorted(groups)), demo_mode, limit)
    now = time.time()
    cached = _CACHE.get(key)
    if cached and (now - cached[2] < CACHE_TTL):
        return cached[0], cached[1]
    catalog, source = load_catalog(groups, demo_mode=demo_mode, limit=limit)
    objects = build_tracked_objects(catalog)
    _CACHE[key] = (objects, source, now)
    return objects, source


# =====================================================================
# 1. Health & Catalog Status Endpoints
# =====================================================================

@app.get("/api/health", summary="Health Check", description="Report API health and live catalog state.")
def health_check():
    meta = get_catalog_metadata("active")
    return {
        "status": "ok",
        "risk_model_loaded": True,
        "risk_scoring_mode": "heuristic-sgp4",
        "catalog_size": meta.get("count", 0),
        "catalog_group": meta.get("group", "active"),
        "catalog_last_updated_utc": meta.get("last_updated_utc"),
        "catalog_age_seconds": meta.get("age_seconds", 0.0),
    }


@app.get("/api/status", summary="Legacy Health Status", include_in_schema=False)
def legacy_status():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/catalog/status", summary="Catalog Status", description="Return detailed metadata about the currently cached TLE catalog.")
def catalog_status():
    return get_catalog_metadata("active")


# =====================================================================
# 2. Catalog Management Endpoints
# =====================================================================

@app.post("/api/catalog/refresh", summary="Refresh Catalog", description="Fetch the latest TLE catalog for a single CelesTrak group and sync to DB partition.")
def refresh_catalog(
    group: str = Query(
        "active",
        description="CelesTrak group name. Supported: active, visual, stations, analyst, fengyun-1c-debris, cosmos-2251-debris, iridium-33-debris, last-30-days, starlink, oneweb, debris, gps-ops, galileo",
    )
):
    objs, src = fetch_group(group, force_refresh=True)
    if not objs:
        raise HTTPException(status_code=502, detail=f"Failed to fetch elements from CelesTrak for group '{group}'.")
    return {
        "status": "success",
        "group": group,
        "source": src,
        "count": len(objs),
        "refreshed_at_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/catalog/refresh-multi", summary="Refresh Catalog Multi", description="Fetch and merge TLE catalogs from multiple CelesTrak groups and sync to DB partitions.")
def refresh_catalog_multi(
    groups: List[str] = Query(
        default=["stations", "active"],
        description="CelesTrak group names to fetch and merge (deduplicates by NORAD ID).",
    )
):
    total = 0
    results = {}
    for g in groups:
        objs, src = fetch_group(g, force_refresh=True)
        count = len(objs) if objs else 0
        results[g] = {"count": count, "source": src}
        total += count

    return {
        "status": "success",
        "groups_refreshed": results,
        "total_downloaded": total,
        "refreshed_at_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/catalog", response_model=List[SatelliteRecord], summary="Get Catalog Endpoint", description="Return all objects in the satellite catalog, optionally filtered by category partition.")
def get_catalog(
    limit: Optional[int] = Query(None, description="Optional maximum number of satellites to return (defaults to all)"),
    group: Optional[str] = Query(None, description="Optional category partition to query (e.g. 'starlink', 'stations')"),
):
    groups = [group] if group else ["stations", "cosmos-1408-debris", "iridium-33-debris", "cosmos-2251-debris"]
    objects, _ = _refresh(groups, demo_mode=False, limit=limit)
    records = []
    for obj in objects:
        records.append(
            SatelliteRecord(
                norad_id=obj.norad_id,
                name=obj.name,
                line1=obj.line1,
                line2=obj.line2,
                apogee_km=obj.apogee_km,
                perigee_km=obj.perigee_km,
                inclination_deg=obj.inclination_deg,
                bstar_drag=obj.bstar_drag,
            )
        )
    return records


@app.get(
    "/tle/bulk",
    response_model=List[TleBulkRecord],
    summary="Tle Bulk",
    description="Return all TLE records for the given CelesTrak group. Each record: { name, line1, line2, catalog_number }. Cached server-side for 2 hours. Designed for client-side SGP4 propagation.",
)
@app.get(
    "/api/tle/bulk",
    response_model=List[TleBulkRecord],
    summary="Tle Bulk (API Prefix)",
    include_in_schema=False,
)
def get_tle_bulk(
    group: str = Query(
        "active",
        description="CelesTrak group, e.g. active / starlink / stations",
    )
):
    group_val = group if isinstance(group, str) else "active"
    records = fetch_bulk_tle(group=group_val)
    return [TleBulkRecord(**r) for r in records]


@app.get("/api/groups", summary="Supported Groups", include_in_schema=False)
def get_groups():
    return SUPPORTED_GROUPS


# =====================================================================
# 3. Ground-Tracks & Orbit Propagation Endpoints
# =====================================================================

@app.get("/api/orbit-tracks", response_model=OrbitTracksResponse, summary="Get Orbit Tracks", description="Return propagated ground-tracks for up to *limit* catalog satellites.")
def get_orbit_tracks(
    hours: float = Query(3.0, gt=0, le=24, description="How many hours ahead to propagate each orbit."),
    step_minutes: float = Query(2.0, gt=0.1, le=30, description="Time step between track points (minutes). Smaller = smoother curve."),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of satellites to include (sliced from the catalog)."),
):
    now = datetime.now(timezone.utc)
    objects, source = _refresh(["stations", "cosmos-1408-debris", "iridium-33-debris", "cosmos-2251-debris"], limit=limit)

    satellites: List[SatelliteOrbitTrack] = []
    for obj in objects[:limit]:
        points = sample_ground_track(obj, now, hours, step_minutes)
        satellites.append(
            SatelliteOrbitTrack(
                norad_id=obj.norad_id,
                name=obj.name,
                inclination_deg=obj.inclination_deg,
                apogee_km=obj.apogee_km,
                perigee_km=obj.perigee_km,
                track=points,
            )
        )

    meta = get_catalog_metadata("active")
    return OrbitTracksResponse(
        generated_at_utc=now.isoformat(),
        propagation_hours=hours,
        step_minutes=step_minutes,
        catalog_group="active",
        catalog_last_updated_utc=meta.get("last_updated_utc"),
        satellite_count=len(satellites),
        satellites=satellites,
    )


@app.get("/api/satellites/{norad_id}/track", response_model=SatelliteOrbitTrack, summary="Get Single Satellite Track", description="Return the propagated ground-track for a single satellite by NORAD ID.")
def get_single_satellite_track(
    norad_id: int = FPath(..., description="NORAD Catalog Number of the satellite."),
    hours: float = Query(3.0, gt=0, le=24, description="Hours to propagate forward."),
    step_minutes: float = Query(2.0, gt=0.1, le=30, description="Step size in minutes."),
):
    objects, _ = _refresh(["active", "stations", "cosmos-1408-debris", "iridium-33-debris", "cosmos-2251-debris"], limit=None)
    target = next((o for o in objects if o.norad_id == norad_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Satellite NORAD {norad_id} not found in active catalog.")

    now = datetime.now(timezone.utc)
    points = sample_ground_track(target, now, hours, step_minutes)
    return SatelliteOrbitTrack(
        norad_id=target.norad_id,
        name=target.name,
        inclination_deg=target.inclination_deg,
        apogee_km=target.apogee_km,
        perigee_km=target.perigee_km,
        track=points,
    )


@app.get("/api/orbits", summary="Legacy 3D Orbits Endpoint", include_in_schema=False)
def legacy_orbits(
    groups_param: str = Query("stations,cosmos-1408-debris,iridium-33-debris,cosmos-2251-debris", alias="groups"),
    demo: bool = Query(False),
    hours: float = Query(1.5, ge=0.1, le=6),
    step_s: int = Query(90, ge=10, le=600),
):
    param_str = groups_param if isinstance(groups_param, str) else "stations,cosmos-1408-debris,iridium-33-debris,cosmos-2251-debris"
    demo_val = demo if isinstance(demo, bool) else False
    hours_val = hours if isinstance(hours, (int, float)) else 1.5
    step_val = step_s if isinstance(step_s, int) else 90

    group_list = [g for g in param_str.split(",") if g]
    objects, source = _refresh(group_list, demo_mode=demo_val)
    now = datetime.now(timezone.utc)
    tracks = []
    for obj in objects:
        pts = sample_track(obj, now, hours_val, step_val)
        tracks.append({
            "name": obj.name,
            "norad_id": obj.norad_id,
            "points": [p[1].tolist() for p in pts],
            "object_type": obj.object_type,
            "group": obj.group,
        })
    return {"source": source, "generated_at": now.isoformat(), "tracks": tracks}


# =====================================================================
# 4. Conjunction Screening & Risk Endpoints
# =====================================================================

@app.get("/api/conjunctions/scan", response_model=List[ConjunctionAlert], summary="Run Conjunction Scan", description="Ultra-fast parallel conjunction scan powered by altitude-shell prefiltering and SGP4.")
def run_conjunction_scan(
    max_candidates: int = Query(50, ge=2, le=200, description="Catalog subset size (smaller = faster demo; larger = more coverage)."),
    miss_distance_cutoff_km: float = Query(25.0, gt=0, le=100, description="Flag any pair whose predicted separation drops below this value (km)."),
    hours: float = Query(12.0, gt=0, le=72, description="Conjunction search window (hours from now)."),
):
    now = datetime.now(timezone.utc)
    objects, _ = _refresh(["stations", "cosmos-1408-debris", "iridium-33-debris", "cosmos-2251-debris"], limit=max_candidates)
    events = find_conjunctions(
        objects,
        now,
        hours=hours,
        threshold_km=miss_distance_cutoff_km,
        coarse_step_s=300,
        max_pairs=3000,
        catalog_group="active",
    )
    # Persist in storage
    storage.record_conjunctions(events)
    # Convert to response models
    alerts = []
    for e in events:
        alerts.append(ConjunctionAlert(**e))
    return alerts


@app.get("/api/conjunctions", summary="Get Recent Conjunctions", description="Return persisted conjunction alerts or evaluate active window.")
def get_conjunctions(
    limit: int = Query(100, ge=1, le=1000),
    group: Optional[str] = Query(None, description="Optional category partition to filter by"),
    groups: Optional[str] = Query(None, description="Legacy parameter for dashboard scan"),
    demo: bool = Query(False),
    hours: Optional[float] = Query(None),
    threshold_km: Optional[float] = Query(None),
):
    groups_val = groups if isinstance(groups, str) else None
    hours_val = hours if isinstance(hours, (int, float)) else None
    thresh_val = threshold_km if isinstance(threshold_km, (int, float)) else None
    demo_val = demo if isinstance(demo, bool) else False
    limit_val = limit if isinstance(limit, int) else 100
    group_val = group if isinstance(group, str) else None

    # If called with legacy query parameters from the frontend dashboard
    if hours_val is not None or groups_val is not None or thresh_val is not None:
        window_hours = hours_val or 48.0
        cutoff_km = thresh_val or 25.0
        group_list = [g for g in (groups_val or "stations,cosmos-1408-debris,iridium-33-debris,cosmos-2251-debris").split(",") if g]
        objects, source = _refresh(group_list, demo_mode=demo_val, limit=80)
        now = datetime.now(timezone.utc)
        raw_events = find_conjunctions(objects, now, window_hours, threshold_km=cutoff_km, catalog_group=group_val or "active")
        storage.record_conjunctions(raw_events)

        scored = []
        by_norad = {o.norad_id: o for o in objects}
        for ev in raw_events:
            age_a = by_norad[ev["sat1_id"]].epoch_age_days(now) if ev["sat1_id"] in by_norad else 1.0
            age_b = by_norad[ev["sat2_id"]].epoch_age_days(now) if ev["sat2_id"] in by_norad else 1.0
            score = score_event(ev["miss_distance_km"], ev["relative_velocity_km_s"], cutoff_km, age_a, age_b)
            scored.append({**ev, **score})

        kessler = kessler_index(scored, total_objects=len(objects))
        return {
            "source": source,
            "generated_at": now.isoformat(),
            "window_hours": window_hours,
            "threshold_km": cutoff_km,
            "object_count": len(objects),
            "kessler_index": kessler,
            "events": scored,
        }

    # Standard endpoint: Return recent stored conjunctions
    stored = storage.get_recent_conjunctions(limit=limit_val, group=group_val)
    return [ConjunctionAlert(**s) for s in stored]


@app.get("/api/export/alerts.csv", summary="Export Alerts CSV", include_in_schema=False)
def export_alerts_csv(
    groups_param: str = Query("stations,cosmos-1408-debris,iridium-33-debris,cosmos-2251-debris", alias="groups"),
    demo: bool = Query(False),
    hours: float = Query(48, ge=1, le=168),
    threshold_km: float = Query(25.0, ge=1, le=200),
):
    data = get_conjunctions(groups=groups_param, demo=demo, hours=hours, threshold_km=threshold_km)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Event ID", "Object A", "NORAD A", "Object B", "NORAD B",
        "TCA (UTC)", "Miss Distance (km)", "Relative Velocity (km/s)",
        "Risk Score", "Risk Level"
    ])
    for e in data.get("events", []):
        writer.writerow([
            e.get("id"),
            e.get("sat1_name") or e.get("object_a", {}).get("name"),
            e.get("sat1_id") or e.get("object_a", {}).get("norad_id"),
            e.get("sat2_name") or e.get("object_b", {}).get("name"),
            e.get("sat2_id") or e.get("object_b", {}).get("norad_id"),
            e.get("tca_utc") or e.get("tca"),
            e.get("miss_distance_km"),
            e.get("relative_velocity_km_s"),
            e.get("risk_score"),
            e.get("risk_level"),
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=astrail_conjunction_report.csv"},
    )


# =====================================================================
# 5. Satellite Logs & Bookmarks Endpoints
# =====================================================================

@app.get("/api/logs/recently-viewed", response_model=List[RecentlyViewedSatellite], summary="Get Recently Viewed", description="Return the last 10 recently viewed satellites from the log file.")
def get_recently_viewed():
    return storage.get_recently_viewed()


@app.post("/api/logs/recently-viewed", response_model=List[RecentlyViewedSatellite], summary="Record Viewed Satellite", description="Record a satellite as viewed, updating the last-10 log on disk.")
def record_viewed_satellite(body: RecordViewRequest = Body(...)):
    data = body.model_dump()
    data["viewed_at"] = datetime.now(timezone.utc).isoformat()
    return storage.record_viewed(data)


@app.delete("/api/logs/recently-viewed", summary="Clear Recent Views", description="Clear all entries from the recently viewed log.")
def clear_recent_views():
    return storage.clear_recent_views()


@app.get("/api/logs/saved-satellites", response_model=List[SavedSatellite], summary="Get Saved Satellites", description="Return all satellites in the collective save/track log.")
def get_saved_satellites():
    return storage.get_saved_satellites()


@app.post("/api/logs/saved-satellites", response_model=SavedSatellite, summary="Save Satellite", description="Save or update a satellite in the collective track log.")
def save_satellite(body: SaveSatelliteRequest = Body(...)):
    data = body.model_dump()
    return storage.save_satellite(data)


@app.delete("/api/logs/saved-satellites/{norad_id}", summary="Delete Saved Satellite", description="Remove a satellite from the collective save/track log.")
def delete_saved_satellite(norad_id: int = FPath(..., description="NORAD Catalog Number of the satellite to remove.")):
    res = storage.delete_saved_satellite(norad_id)
    if res.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=f"Satellite NORAD {norad_id} not found in saved log.")
    return res


@app.get("/api/logs/download/{log_type}", summary="Download Log File", description="Download the specified satellite log file (JSON or formatted .log).")
def download_log_file(
    log_type: str = FPath(..., description="Log type: 'recently_viewed' or 'saved_satellites'"),
    format: str = Query("json", description="File format: 'json' or 'log'"),
):
    if log_type not in ("recently_viewed", "saved_satellites"):
        raise HTTPException(status_code=400, detail="Invalid log_type. Expected 'recently_viewed' or 'saved_satellites'.")

    fmt = format.lower()
    content = storage.format_log(log_type, fmt)

    if fmt == "json":
        media_type = "application/json"
        filename = f"{log_type}.json"
    else:
        media_type = "text/plain"
        filename = f"{log_type}.log"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# =====================================================================
# 6. Static Files & Root Mount
# =====================================================================

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

@app.get("/", summary="Root")
def root_route():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return PlainTextResponse(index_path.read_text(encoding="utf-8"), media_type="text/html")
    return {"message": "Space Debris Tracking & Collision Risk Engine API is running.", "docs": "/docs"}


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")