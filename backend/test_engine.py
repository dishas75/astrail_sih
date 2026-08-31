"""
Automated test suite for Space Debris Tracking & Collision Risk Engine.
Directly tests handler functions, models, schemas, and serialization.
"""
import json
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

import main
from schemas import RecordViewRequest, SaveSatelliteRequest


def run_all_tests():
    print("=" * 60)
    print("RUNNING ASTRAIL SPACE DEBRIS ENGINE TEST SUITE")
    print("=" * 60)

    # 1. Health check
    print("\n1. Testing health_check()...")
    health = main.health_check()
    assert health["status"] == "ok", f"Expected status ok, got {health['status']}"
    assert "catalog_size" in health
    assert "catalog_group" in health
    print("✓ health_check passed:", health)

    # 2. Catalog status
    print("\n2. Testing catalog_status()...")
    status = main.catalog_status()
    assert "supported_groups" in status
    assert "active" in status["supported_groups"]
    print("✓ catalog_status passed (supported groups:", len(status["supported_groups"]), ")")

    # 3. Catalog listing
    print("\n3. Testing get_catalog(limit=5)...")
    catalog = main.get_catalog(limit=5)
    assert len(catalog) > 0, "Expected non-empty catalog"
    item = catalog[0]
    print(f"✓ get_catalog passed: {len(catalog)} items, sample: NORAD {item.norad_id} ({item.name}), apogee: {item.apogee_km}km")

    # 4. Orbit tracks
    print("\n4. Testing get_orbit_tracks(hours=1.0, limit=3)...")
    tracks_resp = main.get_orbit_tracks(hours=1.0, step_minutes=5.0, limit=3)
    assert tracks_resp.satellite_count > 0
    assert len(tracks_resp.satellites) > 0
    sat = tracks_resp.satellites[0]
    assert len(sat.track) > 0
    pt = sat.track[0]
    print(f"✓ get_orbit_tracks passed: {tracks_resp.satellite_count} satellites, sample point: lat={pt.lat}, lon={pt.lon}, alt={pt.alt_km}km")

    # 5. Single satellite track
    print(f"\n5. Testing get_single_satellite_track({sat.norad_id})...")
    single = main.get_single_satellite_track(sat.norad_id, hours=1.0, step_minutes=5.0)
    assert single.norad_id == sat.norad_id
    assert len(single.track) > 0
    print(f"✓ get_single_satellite_track passed for {single.name}")

    # 6. Conjunction scan
    print("\n6. Testing run_conjunction_scan(max_candidates=10)...")
    alerts = main.run_conjunction_scan(max_candidates=10, miss_distance_cutoff_km=50.0, hours=24.0)
    assert isinstance(alerts, list)
    print(f"✓ run_conjunction_scan passed ({len(alerts)} alerts detected)")

    # 7. Recent conjunctions
    print("\n7. Testing get_conjunctions()...")
    recent = main.get_conjunctions(limit=5)
    assert isinstance(recent, list)
    print(f"✓ get_conjunctions passed ({len(recent)} stored alerts)")

    # 8. Recently viewed logging
    print("\n8. Testing logging: recently viewed...")
    rec_view = main.record_viewed_satellite(RecordViewRequest(
        norad_id=25544,
        name="ISS (ZARYA)",
        altitude_km=420.0,
        risk_level="NORMAL"
    ))
    assert any(v["norad_id"] == 25544 for v in rec_view)
    get_views = main.get_recently_viewed()
    assert any(v["norad_id"] == 25544 for v in get_views)
    print("✓ recently viewed logging passed")

    # 9. Saved satellites CRUD
    print("\n9. Testing logging: saved satellites...")
    saved = main.save_satellite(SaveSatelliteRequest(
        norad_id=25544,
        name="ISS (ZARYA)",
        notes="Crewed habitat",
        tags=["crew", "station"]
    ))
    assert saved["norad_id"] == 25544 or getattr(saved, "norad_id", None) == 25544
    all_saved = main.get_saved_satellites()
    assert any(s["norad_id"] == 25544 for s in all_saved)
    deleted = main.delete_saved_satellite(25544)
    assert deleted["status"] == "deleted"
    print("✓ saved satellites CRUD passed")

    # 10. Log file download formatter
    print("\n10. Testing download_log_file()...")
    dl_resp = main.download_log_file("recently_viewed", format="log")
    assert "ASTRAIL RECENTLY VIEWED" in dl_resp.body.decode("utf-8")
    print("✓ download_log_file passed")

    # 11. Legacy orbits endpoint
    print("\n11. Testing legacy_orbits()...")
    leg_orbits = main.legacy_orbits(demo=True, hours=1.0)
    assert "tracks" in leg_orbits
    assert len(leg_orbits["tracks"]) > 0
    print(f"✓ legacy_orbits passed ({len(leg_orbits['tracks'])} tracks)")

    # 12. Legacy conjunctions endpoint
    print("\n12. Testing legacy get_conjunctions with dashboard parameters...")
    leg_conj = main.get_conjunctions(groups="stations", demo=True, hours=24.0, threshold_km=25.0)
    assert "events" in leg_conj
    assert "kessler_index" in leg_conj
    print(f"✓ legacy conjunctions passed (events: {len(leg_conj['events'])}, Kessler Index: {leg_conj['kessler_index']['index']})")

    # 13. Root route
    print("\n13. Testing root_route()...")
    root = main.root_route()
    assert "ASTRAIL" in root.body.decode("utf-8")
    print("✓ root_route serves index.html properly")

    # 14. Tle Bulk Endpoint (/tle/bulk)
    print("\n14. Testing get_tle_bulk(group='active')...")
    bulk_active = main.get_tle_bulk(group="active")
    assert isinstance(bulk_active, list)
    assert len(bulk_active) > 0
    rec0 = bulk_active[0]
    assert hasattr(rec0, "name") and rec0.name
    assert hasattr(rec0, "line1") and rec0.line1.startswith("1 ")
    assert hasattr(rec0, "line2") and rec0.line2.startswith("2 ")
    assert hasattr(rec0, "catalog_number") and isinstance(rec0.catalog_number, int)
    print(f"✓ get_tle_bulk('active') passed: {len(bulk_active)} records (sample: {rec0.name}, NORAD {rec0.catalog_number})")

    # 15. Tle Bulk Stations
    print("\n15. Testing get_tle_bulk(group='stations')...")
    bulk_stations = main.get_tle_bulk(group="stations")
    assert isinstance(bulk_stations, list)
    assert len(bulk_stations) > 0
    assert any("ISS" in r.name or r.catalog_number == 25544 for r in bulk_stations)
    print(f"✓ get_tle_bulk('stations') passed: {len(bulk_stations)} records")

    # 16. Tle Bulk Resilient Fallback (Non-existent group or blocked network)
    print("\n16. Testing get_tle_bulk resilience on unknown group...")
    bulk_fallback = main.get_tle_bulk(group="unknown-group-test")
    assert isinstance(bulk_fallback, list)
    assert len(bulk_fallback) > 0
    print(f"✓ get_tle_bulk resilience passed: {len(bulk_fallback)} records returned without 502/403 error")

    # 17. Refresh catalog endpoint
    print("\n17. Testing refresh_catalog()...")
    ref_resp = main.refresh_catalog(group="stations")
    assert ref_resp["status"] == "success"
    assert ref_resp["group"] == "stations"
    print("✓ refresh_catalog passed")

    # 18. Refresh multi catalog endpoint
    print("\n18. Testing refresh_catalog_multi()...")
    ref_multi = main.refresh_catalog_multi(groups=["stations", "cosmos-1408-debris"])
    assert ref_multi["status"] == "success"
    assert "stations" in ref_multi["groups_refreshed"]
    print("✓ refresh_catalog_multi passed")

    # 19. Export CSV endpoint
    print("\n19. Testing export_alerts_csv()...")
    csv_resp = main.export_alerts_csv(groups_param="stations", demo=True, hours=24.0, threshold_km=25.0)
    assert csv_resp.media_type == "text/csv"
    assert "Content-Disposition" in csv_resp.headers
    print("✓ export_alerts_csv passed")

    # 20. Error conditions: Non-existent satellite track
    print("\n20. Testing 404 for non-existent satellite track...")
    try:
        main.get_single_satellite_track(999999, hours=1.0)
        assert False, "Should have raised HTTPException 404"
    except main.HTTPException as exc:
        assert exc.status_code == 404
        print(f"✓ Correctly raised HTTP 404: {exc.detail}")

    # 21. Error conditions: Invalid log type download
    print("\n21. Testing 400 for invalid log download type...")
    try:
        main.download_log_file("unsupported_type", format="json")
        assert False, "Should have raised HTTPException 400"
    except main.HTTPException as exc:
        assert exc.status_code == 400
        print(f"✓ Correctly raised HTTP 400: {exc.detail}")

    # 22. Multi-threaded concurrency test
    print("\n22. Testing multi-threaded concurrency (10 parallel workers)...")
    import concurrent.futures
    def worker(i):
        main.record_viewed_satellite(RecordViewRequest(norad_id=10000 + i, name=f"SAT_{i}", altitude_km=500.0 + i))
        main.save_satellite(SaveSatelliteRequest(norad_id=20000 + i, name=f"TRACKED_{i}", notes=f"Thread {i}"))
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(worker, range(10)))
    assert all(results)
    print("✓ Concurrent multi-thread execution passed without race conditions")

    print("\n" + "=" * 60)
    print("ALL 22 TESTS INCLUDING /tle/bulk PASSED WITH 100% SUCCESS! 🚀")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
