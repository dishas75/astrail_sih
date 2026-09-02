# ASTRAIL
### Orbital Conjunction & Satellite Collision Risk Watch
**PS-04 — Space Debris Tracking & Satellite Collision Risk Prediction Dashboard**

ASTRAIL ingests public TLE (Two-Line Element) data, propagates every tracked
object with SGP4, screens every object pair for close approaches over a
configurable look-ahead window, scores each conjunction's collision risk,
and renders it all on a mission-control-style dashboard: a 3D orbital
viewer, a risk radar, a live alert feed, a timeline, and a single
headline **Kessler Index** dial.

---

## What makes this build stand out

- **Kessler Index** — one 0–100 "mission control" number that aggregates
  the whole catalog's risk (worst events + conjunction density), not just
  a flat list of alerts.
- **3D orbital viewer**, built from scratch on raw Three.js (no OrbitControls
  dependency — hand-rolled drag/zoom camera) with risk-highlighted orbit
  paths and a glowing marker on any object involved in an active alert.
  Click any marker to inspect it.
- **Risk radar** — a 2D miss-distance vs. time-to-closest-approach scatter,
  bubble-sized and colored by risk, for judges who want the "spreadsheet
  view" of the same data.
- **Conjunction timeline** — a scrubbable strip of every upcoming close
  approach across the look-ahead window.
- **Offline Demo Mode** — a synthetic, physically-propagated TLE catalog
  (checksum-valid, SGP4-parseable) with two *numerically verified*
  engineered close approaches, so the dashboard always has real alerts to
  show even with zero internet at a venue. Flip the toggle back off to
  pull live CelesTrak data any time.
- **Disk-cached live ingestion** — CelesTrak calls are cached for 30 min
  and fall back to the last good cache (then to Demo Mode) if CelesTrak is
  unreachable, so the app never shows a blank dashboard.
- **One-click CSV conjunction report export**, mission-log terminal feed,
  and a from-scratch space-HUD visual identity (Orbitron/Share Tech Mono,
  animated starfield, scanline overlay, glowing risk gauge) — no default
  chat/dashboard template look.

---

## Architecture

```
astrail/
├── backend/
│   ├── main.py          FastAPI app + REST endpoints, serves the frontend too
│   ├── tle_fetch.py      CelesTrak ingestion, disk caching, fallback logic
│   ├── tle_synth.py       Synthetic offline demo catalog (checksum-valid TLEs)
│   ├── propagate.py      SGP4 wrapper (position/velocity/epoch age)
│   ├── conjunction.py    Two-pass (coarse -> fine) close-approach search
│   ├── risk.py            Per-event risk scoring + catalog-wide Kessler Index
│   └── requirements.txt
└── frontend/
    ├── index.html         Dashboard layout
    ├── style.css          Space-HUD visual design
    └── app.js             Three.js 3D viewer, risk radar, alerts, gauge, timeline
```

**Data flow:** CelesTrak TLEs (or synthetic demo TLEs) → SGP4 propagation →
pairwise coarse close-approach scan (5 min step) → fine refinement (10 s
step) around each candidate minimum → risk scoring → Kessler Index →
JSON API → dashboard.

No database is needed — everything is computed on request and cached
in-process/on-disk for 5–30 minutes, which is plenty for a conjunction
board (orbital geometry doesn't change meaningfully faster than that).

---

## Run it locally

Requires Python 3.10+.

```bash
cd astrail/backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open **http://localhost:8000** — the backend serves the frontend
directly, so there's nothing else to start.

No internet at your venue? Flip **Demo Mode** on in the left panel and hit
**Run Conjunction Scan** — everything still works, fully offline.

---

## Deploying it

Because it's a single FastAPI process serving its own static frontend,
it deploys anywhere that runs Python:

- **Render / Railway / Fly.io** — point the start command at
  `uvicorn main:app --host 0.0.0.0 --port $PORT` from the `backend/` dir.
- **Docker** (minimal example):
  ```dockerfile
  FROM python:3.12-slim
  WORKDIR /app
  COPY backend/requirements.txt backend/requirements.txt
  RUN pip install --no-cache-dir -r backend/requirements.txt
  COPY backend backend
  COPY frontend frontend
  WORKDIR /app/backend
  CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
  ```
- **A plain VM** — `pip install -r requirements.txt` then run uvicorn
  behind nginx/Caddy, or use `gunicorn -k uvicorn.workers.UvicornWorker`.

---

## API reference

| Endpoint | Purpose |
|---|---|
| `GET /api/status` | Health check |
| `GET /api/groups` | Available CelesTrak object-set groups |
| `GET /api/catalog?groups=...&demo=bool` | Current tracked objects + live position |
| `GET /api/orbits?groups=...&demo=bool&hours=&step_s=` | Sampled orbit paths for the 3D/2D viewers |
| `GET /api/conjunctions?groups=...&demo=bool&hours=&threshold_km=` | Scored conjunction events + Kessler Index |
| `GET /api/export/alerts.csv?...` | Downloadable conjunction report |

All parameters are optional; sane defaults are baked in.

---

## Tuning knobs

- **Look-ahead window** (2–168 h) and **alert threshold** (1–100 km) are
  live sliders in the UI — no restart needed.
- **`coarse_step_s`** in `conjunction.py` trades scan speed for the risk of
  missing a very short close approach; 300 s (5 min) is a good default for
  catalogs under ~100 objects.
- **`max_pairs`** in `find_conjunctions` caps worst-case scan time for very
  large object sets — raise it if you extend to full CelesTrak catalogs and
  have the CPU budget.

---

## Data & method notes

- TLE data: [CelesTrak](https://celestrak.org) — free, public, no signup.
- Propagation: SGP4 (`sgp4` Python package), the standard model CelesTrak's
  own elements are designed for.
- Distances are computed in the TEME frame SGP4 natively outputs; since
  both objects in a pair are evaluated in the same frame at the same
  instant, relative distance is frame-independent — no extra ECI/ECEF
  conversion is needed for conjunction screening (only for true Earth-fixed
  ground-track work, which is out of scope here).
- Risk scoring and the Kessler Index are heuristic, transparent, and
  tunable (see `risk.py`) — built for triage and demonstration, not as a
  certified conjunction-assessment replacement for tools like the U.S.
  Space Force's official CARA process.
