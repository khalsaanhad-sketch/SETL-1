# SETL – Smart Emergency Terrain Landing (EFB System)

## Overview

SETL is a real-time decision support system designed to assist pilots during emergency situations by providing terrain-aware landing guidance. It operates as a lightweight Electronic Flight Bag (EFB) application that integrates live aircraft data, terrain analysis, and environmental conditions.

## Architecture

- **Framework**: FastAPI (Python 3.11) with Uvicorn
- **Frontend**: Leaflet.js map, vanilla JavaScript, CSS
- **Templates**: Jinja2
- **WebSockets**: Real-time updates via `websockets` library
- **Port**: 5000
- **Algorithm**: AHP-TOPSIS-v2.2-glide-sigmet pipeline

## Project Structure

```
cloud_app/
├── app.py                  # FastAPI main application
├── templates/index.html    # Main HTML page
├── static/
│   ├── main.js             # Frontend JavaScript (Leaflet + WebSocket)
│   └── styles.css          # Styles
└── services/
    ├── risk_engine.py      # Risk computation based on flight state
    ├── guidance_engine.py  # Terrain-aware landing guidance
    ├── alert_engine.py     # Alert generation
    ├── probability_engine.py # Landing success probability
    ├── decision_engine.py  # AHP → TOPSIS decision pipeline
    ├── options_engine.py   # Cell-aware multi-option decision support (bearing/distance/slope)
    ├── terrain_engine.py   # Terrain data (OpenTopoData API + fallback), returns 3-tuple (slope, roughness, elevation)
    ├── weather_engine.py   # Weather data (Open-Meteo + METAR: gusts, ceiling, precipitation)
    ├── glide_engine.py     # Glide envelope reachability for 40+ aircraft types
    ├── sigmet_engine.py    # NOAA SIGMETs + PIREPs (aviationweather.gov, no key needed)
    ├── notam_engine.py     # NOAA NOTAM API — closed/contaminated runway detection (10-min cache)
    ├── validation_engine.py # Retrospective analytics, log loading, anomaly detection
    └── log_engine.py       # CSV flight logging with extended columns
```

## Data Sources

- **Aircraft Data**: airplanes.live ADS-B API + OpenSky (with credentials)
- **Weather Data**: Open-Meteo (free) + METAR/AVWX fallback
- **Terrain Data**: OpenTopoData SRTM30m (with random fallback)
- **SIGMETs/PIREPs**: NOAA aviationweather.gov (free, no API key)
- **NOTAMs**: NOAA aviationweather.gov NOTAM API (free, 10-min spatial cache)

## API Endpoints

- `GET /api/analytics` — Retrospective analytics from flight logs
- `GET /api/log-tail?n=50` — Last N log entries
- `GET /api/aircraft` — Enriched aircraft feed (type/reg/vs_fpm)
- `WS /ws` — Real-time risk grid, glide mask, SIGMET/PIREP data

## Frontend Features

- **Night Mode**: Toggle dark cockpit-friendly theme
- **Voice Alerts**: Web Speech API alerts with auto-manage (auto-on for CRITICAL/HIGH, auto-off after 5 safe ticks)
- **Glide Overlay**: Shows glide range, reachable/safe cell counts
- **SIGMET Banner**: Fixed top banner when SIGMETs affect area
- **Analytics Modal**: Session stats, risk distribution, decision quality, anomalies
- **Critical Pulse**: Panel border animation on CRITICAL risk level

## Running the App

```
uvicorn cloud_app.app:app --host 0.0.0.0 --port 5000
```

## Key Notes

- WebSocket connections use `wss://` on HTTPS (Replit) and `ws://` on HTTP
- Session state is in-memory (dictionary), not persisted
- Deployed as VM (always-running) to support WebSocket connections
- No API keys required — all external APIs are public/free
- Terrain engine returns 3-tuple: `(slope_grid, roughness_grid, elev_grid)` — all callers must destructure
- Slope threshold: 15° (aviation standard), terrain clearance floors: <200ft→0.92, <500ft→0.72, <1000ft→0.46
- CSV log columns include glide metrics, SIGMET data, and extended aircraft fields
- Risk engine: vs_fpm vertical speed risk (0.04–0.30), QNH pressure correction for true altitude, TTG time-to-ground scalar (1.0–1.40)
- NOTAM engine: 10-min spatial cache, detects CLOSED (−0.20 runway bonus) and CONTAMINATED (−0.10) airports
- Glide engine: per-cell bearing wind computation (each cell gets tailwind/headwind based on bearing from aircraft)
- Weather engine: haversine station selection (not Euclidean), current UTC hour hourly index, QNH from METAR altimeter setting
- Decision engine: TOPSIS uses explicit cost_cols parameter; dist_cost = |dist-1.5|/dist penalizes both too-close and too-far cells
- Session management: TTL eviction (1 hour), 500-session cap, input validation (float/string/bool whitelisting)
- WebSocket reconnect: exponential backoff 1.5s → 30s cap (resets on successful open)
