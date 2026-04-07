# SETL – Smart Emergency Terrain Landing (EFB System)

## Overview

SETL is a real-time decision support system designed to assist pilots during emergency situations by providing terrain-aware landing guidance. It operates as a lightweight Electronic Flight Bag (EFB) application that integrates live aircraft data, terrain analysis, and environmental conditions.

## Architecture

- **Framework**: FastAPI (Python 3.11) with Uvicorn
- **Frontend**: Leaflet.js map, vanilla JavaScript, CSS
- **Templates**: Jinja2
- **WebSockets**: Real-time updates via `websockets` library
- **Port**: 5000

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
    ├── options_engine.py   # Multi-option decision support
    ├── terrain_engine.py   # Terrain data (OpenTopoData API + fallback)
    └── weather_engine.py   # Weather data (Open-Meteo API)
```

## Data Sources

- **Aircraft Data**: airplanes.live ADS-B API
- **Weather Data**: Open-Meteo (free, no API key needed)
- **Terrain Data**: OpenTopoData SRTM30m (with random fallback)

## Running the App

```
uvicorn cloud_app.app:app --host 0.0.0.0 --port 5000
```

## Key Notes

- WebSocket connections use `wss://` on HTTPS (Replit) and `ws://` on HTTP
- Session state is in-memory (dictionary), not persisted
- Deployed as VM (always-running) to support WebSocket connections
- No API keys required — all external APIs are public/free
