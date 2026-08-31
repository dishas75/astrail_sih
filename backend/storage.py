"""
Storage management for satellite logging, bookmarking, and file downloads.
Stores persistent state in JSON format under backend/_cache/data/.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

DATA_DIR = Path(__file__).parent / "_cache" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

RECENT_VIEWS_FILE = DATA_DIR / "recently_viewed.json"
SAVED_SATELLITES_FILE = DATA_DIR / "saved_satellites.json"
CONJUNCTIONS_STORE_FILE = DATA_DIR / "conjunction_alerts.json"


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data):
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Failed to write storage file {path}: {e}")


class StorageService:
    def __init__(self):
        self._recently_viewed: List[dict] = _read_json(RECENT_VIEWS_FILE, [])
        self._saved_satellites: Dict[str, dict] = _read_json(SAVED_SATELLITES_FILE, {})
        self._conjunction_alerts: List[dict] = _read_json(CONJUNCTIONS_STORE_FILE, [])

    # --- Recently Viewed ---
    def get_recently_viewed(self) -> List[dict]:
        return self._recently_viewed

    def record_viewed(self, record: dict) -> List[dict]:
        # remove existing entry for same norad_id if present
        norad_id = record.get("norad_id")
        self._recently_viewed = [item for item in self._recently_viewed if item.get("norad_id") != norad_id]
        # prepend newest
        self._recently_viewed.insert(0, record)
        # keep last 10
        self._recently_viewed = self._recently_viewed[:10]
        _write_json(RECENT_VIEWS_FILE, self._recently_viewed)
        return self._recently_viewed

    def clear_recent_views(self) -> dict:
        self._recently_viewed = []
        _write_json(RECENT_VIEWS_FILE, self._recently_viewed)
        return {"status": "cleared", "count": 0}

    # --- Saved Satellites ---
    def get_saved_satellites(self) -> List[dict]:
        return list(self._saved_satellites.values())

    def save_satellite(self, req: dict) -> dict:
        norad_id = str(req.get("norad_id"))
        now_str = datetime.now(timezone.utc).isoformat()
        existing = self._saved_satellites.get(norad_id)
        if existing:
            existing.update(req)
            existing["updated_at"] = now_str
            self._saved_satellites[norad_id] = existing
        else:
            req["added_at"] = now_str
            req["updated_at"] = None
            self._saved_satellites[norad_id] = req

        _write_json(SAVED_SATELLITES_FILE, self._saved_satellites)
        return self._saved_satellites[norad_id]

    def delete_saved_satellite(self, norad_id: int) -> dict:
        key = str(norad_id)
        if key in self._saved_satellites:
            deleted = self._saved_satellites.pop(key)
            _write_json(SAVED_SATELLITES_FILE, self._saved_satellites)
            return {"status": "deleted", "norad_id": norad_id, "name": deleted.get("name")}
        return {"status": "not_found", "norad_id": norad_id}

    # --- Conjunction Alerts History ---
    def get_recent_conjunctions(self, limit: int = 100, group: Optional[str] = None) -> List[dict]:
        alerts = self._conjunction_alerts
        if group:
            alerts = [a for a in alerts if a.get("catalog_group") == group]
        return alerts[:limit]

    def record_conjunctions(self, alerts: List[dict]):
        if not alerts:
            return
        # Prepend new alerts and deduplicate by id
        seen_ids = set()
        combined = []
        for alert in alerts + self._conjunction_alerts:
            aid = alert.get("id")
            if aid not in seen_ids:
                seen_ids.add(aid)
                combined.append(alert)
        self._conjunction_alerts = combined[:500]
        _write_json(CONJUNCTIONS_STORE_FILE, self._conjunction_alerts)

    # --- Download Formatter ---
    def format_log(self, log_type: str, fmt: str) -> str:
        if log_type == "recently_viewed":
            data = self.get_recently_viewed()
            title = "ASTRAIL RECENTLY VIEWED SATELLITES LOG"
        elif log_type == "saved_satellites":
            data = self.get_saved_satellites()
            title = "ASTRAIL TRACKED & SAVED SATELLITES LOG"
        else:
            data = []
            title = f"ASTRAIL LOG: {log_type.upper()}"

        if fmt == "json":
            return json.dumps(data, indent=2)

        # formatted text log
        lines = [
            "=" * 70,
            f"{title}",
            f"Generated: {datetime.now(timezone.utc).isoformat()} UTC",
            f"Total Entries: {len(data)}",
            "=" * 70,
            "",
        ]
        for i, item in enumerate(data, start=1):
            lines.append(f"[{i:02d}] NORAD {item.get('norad_id')} - {item.get('name')}")
            for k, v in item.items():
                if k not in ("norad_id", "name") and v is not None:
                    lines.append(f"     {k}: {v}")
            lines.append("-" * 40)

        return "\n".join(lines)


storage = StorageService()

