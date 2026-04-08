from fastapi import FastAPI, WebSocket, Request, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio
import base64
import httpx
import os
import time
import uuid
import random

from cloud_app.services.risk_engine import compute_risk
from cloud_app.services.guidance_engine import compute_guidance
from cloud_app.services.alert_engine import compute_alerts
from cloud_app.services.probability_engine import compute_probability
from cloud_app.services.options_engine import compute_options
from cloud_app.services.terrain_engine import get_terrain, get_terrain_grid
from cloud_app.services.weather_engine import get_weather
from cloud_app.services.decision_engine import score_cells

app = FastAPI()

templates = Jinja2Templates(directory="cloud_app/templates")


app.mount("/static", StaticFiles(directory="cloud_app/static"), name="static")


# ── main.js: served from a non-static path so no ETag/304 caching occurs ──────
@app.get("/js/main.js", include_in_schema=False)
async def serve_main_js():
    return FileResponse(
        "cloud_app/static/main.js",
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

sessions = {}


def ensure_session(sid):
    if sid not in sessions:
        sessions[sid] = {
            "latitude": 28.6139,
            "longitude": 77.2090,
            "altitude_ft": 5000,
            "speed_kts": 100,
            "heading_deg": 90,
        }
    return sessions[sid]


def generate_cells(state, terrain, prob, weather=None,
                   slope_grid=None, roughness_grid=None):
    """
    Build a 9×9 risk grid centred on the aircraft.

    Scoring pipeline (when slope/roughness grids are available):
        AHP weights → TOPSIS relative ranking → Logistic → risk = 1 - probability

    Falls back to the elevation-derived slope from terrain_engine when the
    DEM grid could not be fetched (API timeout, rate-limit, etc.).

    Output format is unchanged — the frontend receives the same cell dict
    structure as before.
    """
    lat        = state["latitude"]
    lon        = state["longitude"]
    is_water   = terrain.get("is_water", False)
    base_slope = terrain.get("slope_deg", 0.0)
    elev       = terrain.get("elevation_m", 0.0)
    wind_ms    = float((weather or {}).get("wind_speed", 5.0))

    steps = 4
    size  = 0.01

    # ── Distance helper (flat-earth, km) ──────────────────────────────────────
    def dist_km(i, j):
        return ((i * size * 111.0) ** 2 + (j * size * 111.0) ** 2) ** 0.5

    # ── Build cell list with all feature fields ───────────────────────────────
    cells = []
    for i in range(-steps, steps + 1):
        for j in range(-steps, steps + 1):
            cell_lat = lat + i * size
            cell_lon = lon + j * size
            gi, gj   = i + steps, j + steps   # grid indices (0–8)

            corners = [
                [cell_lat,        cell_lon],
                [cell_lat + size, cell_lon],
                [cell_lat + size, cell_lon + size],
                [cell_lat,        cell_lon + size],
            ]

            if is_water:
                local_depth = max(0.0, abs(elev) + random.uniform(-150, 150))
                cells.append({
                    "corners":   corners,
                    "is_water":  True,
                    "depth_m":   round(local_depth, 0),
                    # TOPSIS inputs
                    "slope":     0.0,
                    "roughness": 0.0,
                    "distance":  dist_km(i, j),
                    "wind":      wind_ms,
                    "crowd":     0.0,
                    "obstacle":  0.0,
                    # placeholders — overwritten by score_cells()
                    "risk":      0.9,
                    "color":     "#1d4ed8",
                })
            else:
                # Per-cell slope from DEM grid when available; else base value
                if slope_grid is not None:
                    slope = max(0.0, float(slope_grid[gi, gj])
                                + random.uniform(-0.5, 0.5))
                else:
                    slope = max(0.0, base_slope + random.uniform(-1.0, 1.0))

                roughness = float(roughness_grid[gi, gj]) if roughness_grid is not None else 0.0

                cells.append({
                    "corners":   corners,
                    "is_water":  False,
                    # TOPSIS inputs
                    "slope":     round(slope, 2),
                    "roughness": round(roughness, 2),
                    "distance":  dist_km(i, j),
                    "wind":      wind_ms,
                    "crowd":     0.0,
                    "obstacle":  0.0,
                    # placeholders — overwritten by score_cells()
                    "risk":      0.5,
                    "color":     "#d8d62b",
                })

    # ── AHP → TOPSIS → Logistic ───────────────────────────────────────────────
    cells = score_cells(cells)

    # ── Strip internal-only TOPSIS input fields before sending to frontend ────
    for c in cells:
        for key in ("roughness", "distance", "wind", "crowd", "obstacle"):
            c.pop(key, None)

    return cells


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("cloud_app/static/favicon.ico")


# ── OpenSky auth helpers ───────────────────────────────────────────────────────
_osky_token_cache: dict = {"token": None, "expires_at": 0.0}


def _opensky_basic_auth() -> str | None:
    user = os.environ.get("OPENSKY_USER", "").strip()
    pw   = os.environ.get("OPENSKY_PASS", "").strip()
    if user and pw:
        return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    return None


async def _opensky_bearer_token(client: httpx.AsyncClient) -> str | None:
    """Fetch (or return cached) an OAuth2 Bearer token from OpenSky."""
    client_id  = os.environ.get("OPENSKY_CLIENT_ID",     "").strip()
    client_sec = os.environ.get("OPENSKY_CLIENT_SECRET", "").strip()
    if not client_id or not client_sec:
        return None
    now = time.time()
    if _osky_token_cache["token"] and now < _osky_token_cache["expires_at"] - 60:
        return _osky_token_cache["token"]
    try:
        resp = await client.post(
            "https://auth.opensky-network.org/auth/realms/opensky-network"
            "/protocol/openid-connect/token",
            data={
                "grant_type":    "client_credentials",
                "client_id":     client_id,
                "client_secret": client_sec,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=6.0,
        )
        data  = resp.json()
        token = data.get("access_token")
        if token:
            _osky_token_cache["token"]      = token
            _osky_token_cache["expires_at"] = now + data.get("expires_in", 3600)
            return token
    except Exception:
        pass
    return None


async def _opensky_auth_header(client: httpx.AsyncClient) -> str | None:
    """Best available auth: OAuth2 Bearer > Basic Auth > None."""
    token = await _opensky_bearer_token(client)
    if token:
        return f"Bearer {token}"
    return _opensky_basic_auth()


# ── Indian domestic flight detection ──────────────────────────────────────────
# India's ICAO 24-bit address block: 0x800000 – 0x87FFFF
_INDIA_ICAO_LO = 0x800000
_INDIA_ICAO_HI = 0x87FFFF

# ICAO airline designators for Indian carriers (domestic operators)
_INDIAN_CALLSIGN_PREFIXES = {
    "IGO",  # IndiGo
    "AIC",  # Air India
    "SEJ",  # SpiceJet
    "VTI",  # Vistara (now merged into Air India)
    "AXB",  # Akasa Air
    "GOW",  # Go First
    "IAD",  # Air Asia India
    "LLR",  # Alliance Air
    "BDA",  # Blue Dart Aviation
    "SDK",  # Star Air
    "TRJ",  # TruJet
    "FLB",  # FlyBig
    "SHL",  # Shree Airlines
    "DDG",  # Deccan Charters
    "CIL",  # Air Carnival
}


def _is_indian_domestic(s: list) -> bool:
    """Return True if the OpenSky state vector is an Indian-registered aircraft.

    Uses three independent signals — any one match is sufficient:
      1. ICAO24 hex address in India's allocated block (0x800000–0x87FFFF)
      2. origin_country field == "India"
      3. Callsign starts with a known Indian carrier ICAO prefix
    """
    # Signal 1: ICAO24 address block
    try:
        addr = int((s[0] or "").strip(), 16)
        if _INDIA_ICAO_LO <= addr <= _INDIA_ICAO_HI:
            return True
    except (ValueError, TypeError):
        pass

    # Signal 2: origin_country
    if (s[2] or "").strip() == "India":
        return True

    # Signal 3: callsign prefix
    callsign = (s[1] or "").strip().upper()
    if len(callsign) >= 3 and callsign[:3] in _INDIAN_CALLSIGN_PREFIXES:
        return True

    return False


def _parse_opensky_states(states: list) -> tuple[list, list]:
    """Convert OpenSky state vectors → (domestic_ac, international_ac).

    State vector layout:
      [0]=icao24  [1]=callsign  [2]=origin_country
      [5]=lon  [6]=lat  [7]=baro_alt_m
      [8]=on_ground  [9]=vel_m_s  [10]=true_track_deg
    """
    domestic, international = [], []
    for s in states:
        if s[6] is None or s[5] is None or s[8]:   # no position or on ground
            continue
        entry = {
            "hex":      s[0],
            "flight":   (s[1] or "").strip(),
            "lat":      s[6],
            "lon":      s[5],
            "alt_baro": round(s[7] * 3.28084) if s[7] else 0,
            "gs":       round(s[9] * 1.944)   if s[9] else 0,
            "track":    s[10] or 0,
        }
        if _is_indian_domestic(s):
            domestic.append(entry)
        else:
            international.append(entry)
    return domestic, international


@app.get("/api/aircraft")
async def proxy_aircraft(lat: float, lon: float, radius: int = 200):
    d        = 1.8   # ±1.8° ≈ 200 km bounding box for OpenSky
    osky_url = (
        "https://opensky-network.org/api/states/all"
        f"?lamin={lat - d:.4f}&lomin={lon - d:.4f}"
        f"&lamax={lat + d:.4f}&lomax={lon + d:.4f}"
    )
    adsb_urls = [
        f"https://api.airplanes.live/v2/point/{lat}/{lon}/{radius}",
        f"https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{radius}",
    ]

    async with httpx.AsyncClient(timeout=8.0) as client:
        auth = await _opensky_auth_header(client)

        async def fetch_adsb() -> list:
            for url in adsb_urls:
                try:
                    r = await client.get(url, headers={"User-Agent": "SETL-EFB/1.0"})
                    data = r.json()
                    if data.get("ac"):
                        return data["ac"]
                except Exception:
                    continue
            return []

        async def fetch_opensky() -> tuple[list, list]:
            if not auth:
                return [], []
            try:
                r = await client.get(
                    osky_url,
                    headers={"Authorization": auth, "User-Agent": "SETL-EFB/1.0"},
                    timeout=3.0,   # fast fail — Replit IPs often IP-blocked by OpenSky
                )
                return _parse_opensky_states(r.json().get("states") or [])
            except Exception:
                return [], []

        # Run ADS-B and OpenSky in parallel
        adsb_ac, (osky_domestic, osky_intl) = await asyncio.gather(
            fetch_adsb(), fetch_opensky()
        )

        # ── Merge strategy ────────────────────────────────────────────────────
        # Domestic OpenSky is PRIMARY: always included alongside ADS-B.
        # International OpenSky is FALLBACK: only added when ADS-B returns nothing.
        # ADS-B data overwrites OpenSky for any duplicate ICAO24 (richer fields).
        merged: dict[str, dict] = {ac["hex"]: ac for ac in osky_domestic}
        for ac in adsb_ac:
            merged[ac["hex"]] = ac          # ADS-B wins on duplicate

        if not adsb_ac:                     # fallback: no ADS-B at all
            for ac in osky_intl:
                merged.setdefault(ac["hex"], ac)

        return {"ac": list(merged.values())}


@app.get("/api/opensky-creds")
async def opensky_creds():
    """Return auth header for browser-side OpenSky fetches.
    Prefers OAuth2 Bearer; falls back to Basic Auth.
    Credentials never appear in the JS bundle."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        auth = await _opensky_auth_header(client)
    return {"auth": auth}


@app.get("/api/session")
def create_session():
    sid = str(uuid.uuid4())
    ensure_session(sid)
    return {"session_id": sid}


@app.post("/api/live-state/{sid}")
async def update_state(sid: str, request: Request):
    data = await request.json()
    state = ensure_session(sid)
    state.update(data)
    return {"ok": True}


@app.websocket("/ws/{sid}")
async def ws_endpoint(ws: WebSocket, sid: str):
    await ws.accept()
    state = ensure_session(sid)

    try:
        while True:
            try:
                lat, lon = state["latitude"], state["longitude"]

                # Fetch terrain (single-point), weather, and DEM grid in parallel.
                # get_terrain_grid is cached by position so the batch API call
                # only fires when the aircraft has moved >~5 km.
                (terrain, weather, (slope_grid, roughness_grid)) = await asyncio.gather(
                    get_terrain(lat, lon),
                    get_weather(lat, lon),
                    get_terrain_grid(lat, lon),
                )

                risk = compute_risk(state)
                prob = compute_probability(risk)
                options = compute_options(prob)
                alerts = compute_alerts(risk, prob)
                guidance = compute_guidance(state, terrain)

                cells = generate_cells(
                    state, terrain, prob["success"],
                    weather=weather,
                    slope_grid=slope_grid,
                    roughness_grid=roughness_grid,
                )

                result = {
                    "alerts": alerts,
                    "guidance": guidance,
                    "probabilistic": prob,
                    "options": options,
                    "terrain": terrain,
                    "weather": weather,
                    "cells": cells,
                }

                await ws.send_json(result)

            except (WebSocketDisconnect, RuntimeError):
                # Client closed the connection — exit cleanly
                return

            except Exception as e:
                # Recoverable computation/fetch error — log and continue
                print(f"WS frame error (recoverable): {e}")

            # Always pause between ticks, even after an error
            await asyncio.sleep(1.5)

    except Exception as e:
        print("WebSocket fatal error:", e)

    finally:
        try:
            await ws.close()
        except Exception:
            pass
