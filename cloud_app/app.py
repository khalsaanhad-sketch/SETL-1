from fastapi import FastAPI, WebSocket, Request, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio
import base64
import httpx
import math as _math
import os
import time
import uuid

from cloud_app.services.risk_engine import compute_risk
from cloud_app.services.guidance_engine import compute_guidance
from cloud_app.services.alert_engine import compute_alerts
from cloud_app.services.probability_engine import compute_probability
from cloud_app.services.options_engine import compute_options
from cloud_app.services.terrain_engine import get_terrain, get_terrain_grid
from cloud_app.services.weather_engine import get_weather
from cloud_app.services.decision_engine import score_cells
from cloud_app.services.crowd_engine import get_osm_crowd_grid
import cloud_app.services.crowd_engine as _crowd_engine
from cloud_app.services.runway_engine import apply_runway_bonus, get_cached_runways
import cloud_app.services.runway_engine as _runway_engine
from cloud_app.services.log_engine import log_entry, init_log_engine

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

init_log_engine()


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
                   slope_grid=None, roughness_grid=None,
                   crowd_grid=None, obstacle_grid=None):
    """
    Build a 9×9 risk grid.

    Two layout modes selected by state["forward_grid"]:

    • False (area mode, default):
        Grid centred on the aircraft position, north-aligned.
        Aircraft is at the centre cell.

    • True (nose-forward, active aircraft tracking):
        Grid rotated to heading_deg.  Aircraft sits just behind row 8
        (the last/nearest row); all 9 rows extend ahead of the nose.
        Gives the pilot a pure "front vision" view of reachable terrain.

    Scoring pipeline: AHP → TOPSIS → Logistic → risk = 1 − probability
    Output cell dict structure is unchanged in both modes.
    """
    lat        = state["latitude"]
    lon        = state["longitude"]
    is_water       = terrain.get("is_water", False)
    base_slope     = terrain.get("slope_deg", 0.0)
    elev           = terrain.get("elevation_m", 0.0)
    elevation_live = terrain.get("elevation_live", False)
    wind_ms        = float((weather or {}).get("wind_speed_kts", 5.0)) * 0.5144  # kts → m/s

    terrain_conf  = "real" if slope_grid is not None else ("approx" if elevation_live else "low")
    weather_conf  = (weather or {}).get("confidence", "low")

    steps = 4
    size  = 0.01   # degrees per cell (≈ 1.11 km at the equator)

    forward_grid = state.get("forward_grid", False)
    heading_deg  = float(state.get("heading_deg", 0.0) or 0.0)

    # ── Shared cell builder — accepts corner list and grid indices ────────────
    def _build_cell(corners, gi, gj, dist):
        if is_water:
            if elevation_live and elev < 0:
                local_depth      = abs(elev)
                water_confidence = "real"
            else:
                local_depth      = None
                water_confidence = "unknown"

            crowd_val = round(float(crowd_grid[gi, gj]), 3) if crowd_grid is not None else 0.0
            return {
                "corners":            corners,
                "is_water":           True,
                "depth_m":            round(local_depth, 0) if local_depth is not None else None,
                "water_confidence":   water_confidence,
                "terrain_confidence": terrain_conf,
                "weather_confidence": weather_conf,
                "slope":     0.0,
                "roughness": 0.0,
                "distance":  dist,
                "wind":      wind_ms,
                "crowd":     crowd_val,
                "obstacle":  0.0,
                "surface":   1.0,
                "risk":      0.9,
                "color":     "#1d4ed8",
            }
        else:
            slope = max(0.0, float(slope_grid[gi, gj])) if slope_grid is not None else max(0.0, base_slope)
            roughness     = float(roughness_grid[gi, gj]) if roughness_grid is not None else 0.0
            surface_score = round(min(1.0, slope / 30.0), 3)
            crowd_val     = round(float(crowd_grid[gi, gj]),    3) if crowd_grid    is not None else 0.0
            obstacle_val  = round(float(obstacle_grid[gi, gj]), 3) if obstacle_grid is not None else 0.0
            obstacle_conf = "real" if obstacle_grid is not None else "low"
            return {
                "corners":             corners,
                "is_water":            False,
                "terrain_confidence":  terrain_conf,
                "weather_confidence":  weather_conf,
                "obstacle_confidence": obstacle_conf,
                "slope":     round(slope, 2),
                "roughness": round(roughness, 2),
                "distance":  dist,
                "wind":      wind_ms,
                "crowd":     crowd_val,
                "obstacle":  obstacle_val,
                "surface":   surface_score,
                "risk":      0.5,
                "color":     "#d8d62b",
            }

    cells = []

    if forward_grid:
        # ── Nose-forward rotated grid ─────────────────────────────────────────
        # The aircraft sits just behind row 8 (nearest row).
        # Row 0 is the farthest ahead.  Column 4 is dead-centre of the nose.
        # All geometry is in a body frame (fwd = ahead, lat = right-of-heading)
        # then rotated to geographic (Δlat, Δlon).
        heading_rad = _math.radians(heading_deg)
        lat_rad     = _math.radians(lat)
        m_per_lat   = 111320.0
        m_per_lon   = 111320.0 * _math.cos(lat_rad)
        cell_m      = size * m_per_lat          # ≈ 1113 m per cell side

        def to_geo(fwd_m, lat_m):
            """Body (fwd ahead, lat right) → geographic offset (Δlat°, Δlon°)."""
            dn = fwd_m * _math.cos(heading_rad) - lat_m * _math.sin(heading_rad)
            de = fwd_m * _math.sin(heading_rad) + lat_m * _math.cos(heading_rad)
            return dn / m_per_lat, de / m_per_lon

        for r in range(9):      # r=0 farthest ahead, r=8 nearest (closest to nose)
            for c in range(9):  # c=0 left, c=4 centre, c=8 right
                # Forward extent of this cell (metres ahead of aircraft)
                fwd_near = (8 - r) * cell_m
                fwd_far  = (9 - r) * cell_m
                # Lateral extent (metres; negative = left of heading)
                lat_left  = (c - 4.5) * cell_m
                lat_right = (c - 3.5) * cell_m

                # 4 corners: near-left, far-left, far-right, near-right
                dl, dn = to_geo(fwd_near, lat_left);  c0 = [lat + dl, lon + dn]
                dl, dn = to_geo(fwd_far,  lat_left);  c1 = [lat + dl, lon + dn]
                dl, dn = to_geo(fwd_far,  lat_right); c2 = [lat + dl, lon + dn]
                dl, dn = to_geo(fwd_near, lat_right); c3 = [lat + dl, lon + dn]

                # DEM grid indices: approximate mapping to the north-aligned DEM
                gi, gj = r, c

                # Distance from aircraft to cell centre (km)
                fwd_ctr_m = (8.5 - r) * cell_m
                lat_ctr_m = (c - 4.0) * cell_m
                dist = round(((fwd_ctr_m ** 2 + lat_ctr_m ** 2) ** 0.5) / 1000, 2)

                cells.append(_build_cell([c0, c1, c2, c3], gi, gj, dist))

    else:
        # ── Area mode: north-aligned grid centred on aircraft (original) ──────
        def dist_km(i, j):
            return ((i * size * 111.0) ** 2 + (j * size * 111.0) ** 2) ** 0.5

        for i in range(-steps, steps + 1):
            for j in range(-steps, steps + 1):
                cell_lat = lat + i * size
                cell_lon = lon + j * size
                gi, gj   = i + steps, j + steps

                corners = [
                    [cell_lat,        cell_lon],
                    [cell_lat + size, cell_lon],
                    [cell_lat + size, cell_lon + size],
                    [cell_lat,        cell_lon + size],
                ]
                cells.append(_build_cell(corners, gi, gj, dist_km(i, j)))

    # ── AHP → TOPSIS → Logistic ───────────────────────────────────────────────
    cells = score_cells(cells)

    # ── Confidence score: average of all available layer confidences ──────────
    _conf_map = {"real": 1.0, "approx": 0.6, "low": 0.3, "unknown": 0.2}
    _conf_keys = ("terrain_confidence", "weather_confidence",
                  "obstacle_confidence", "water_confidence")
    for c in cells:
        vals = [_conf_map[c[k]] for k in _conf_keys if k in c and c[k] in _conf_map]
        c["confidence_score"] = round(sum(vals) / len(vals), 2) if vals else 0.3

    # ── Strip pure-TOPSIS internals before sending to frontend ──────────────
    # NOTE: "crowd" is intentionally retained so the frontend can display
    #       per-cell crowd density in the Layers panel.
    for c in cells:
        for key in ("roughness", "distance", "wind", "obstacle", "surface"):
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
                _tick_start = time.monotonic()
                lat, lon = state["latitude"], state["longitude"]

                # ── Crowd density: non-blocking background fetch ──────────────
                # Overpass can take 5-20 s on first load for dense cities.
                # Strategy: serve a frame immediately using cached crowd data
                # (or crowd=0 fallback) and fire the Overpass query as a
                # background asyncio Task.  The *next* WS tick will find the
                # cache populated and include full crowd density.
                # This keeps the WS tick rate at terrain-speed (~3-5 s first
                # load) rather than Overpass-speed (5-20 s).
                _ck    = _crowd_engine._grid_cache_key(lat, lon)
                _cache = _crowd_engine._CROWD_CACHE
                if _cache["key"] == _ck and _cache["crowd"] is not None:
                    # Fast path — already cached
                    crowd_grid, obstacle_grid = _cache["crowd"], _cache["obstacle"]
                elif _cache.get("pending") == _ck:
                    # In-flight: background task running, use fallback this tick
                    crowd_grid, obstacle_grid = None, None
                else:
                    # Cache miss — launch background task, use fallback this tick
                    asyncio.create_task(get_osm_crowd_grid(lat, lon))
                    crowd_grid, obstacle_grid = None, None

                # ── Runway data: background load + instant cache read ─────────
                # OurAirports CSV (~2 MB) is downloaded once on the first tick
                # and held in memory for the session.  All subsequent ticks do
                # an instant in-memory filter (<1 ms).  Overpass is used as
                # fallback when OA has no runway in the current area.
                if _runway_engine._OA_DB is None and not _runway_engine._OA_LOADING:
                    _runway_engine._OA_LOADING = True
                    asyncio.create_task(_runway_engine.get_nearby_runways(lat, lon))
                runways = get_cached_runways(lat, lon)

                # Terrain, weather, and DEM run in parallel — none blocked by Overpass
                terrain, weather, (slope_grid, roughness_grid) = await asyncio.gather(
                    get_terrain(lat, lon),
                    get_weather(lat, lon),
                    get_terrain_grid(lat, lon),
                )

                risk = compute_risk(state, weather)
                prob = compute_probability(risk)
                options = compute_options(prob)
                alerts = compute_alerts(risk, prob, weather)
                guidance = compute_guidance(state, terrain, weather)

                cells = generate_cells(
                    state, terrain, prob["success"],
                    weather=weather,
                    slope_grid=slope_grid,
                    roughness_grid=roughness_grid,
                    crowd_grid=crowd_grid,
                    obstacle_grid=obstacle_grid,
                )

                # Post-TOPSIS runway proximity bonus (no-op when runways=[])
                cells = apply_runway_bonus(cells, runways)

                result = {
                    "alerts":       alerts,
                    "guidance":     guidance,
                    "probabilistic": prob,
                    "risk":         risk,   # exposes flight_state + weather_risk to frontend
                    "options":      options,
                    "terrain":      terrain,
                    "weather":      weather,
                    "cells":        cells,
                    # crowd_ready: False means OSM Overpass is still fetching in
                    # the background; frontend shows "Fetching…" instead of 0%.
                    "crowd_ready":   crowd_grid is not None,
                    # runway_ready: True once OurAirports CSV is loaded or
                    # Overpass has returned data for this position.
                    "runway_ready":  bool(runways),
                }

                # ── Pre-log derived fields ────────────────────────────────────
                _tick_ms = round((time.monotonic() - _tick_start) * 1000)

                # Best non-water cell by probability
                _land_cells  = [c for c in cells if not c.get("is_water", False)]
                _best_cell   = max(_land_cells, key=lambda c: c["probability"]) if _land_cells else None
                _best_prob   = round(_best_cell["probability"], 4) if _best_cell else None
                _best_color  = _best_cell.get("color") if _best_cell else None

                # Straight-line distance from aircraft to best-cell centre (nm)
                _best_dist_nm = None
                if _best_cell:
                    _bc = _best_cell["corners"]
                    _bc_lat = (_bc[0][0] + _bc[2][0]) / 2
                    _bc_lon = (_bc[0][1] + _bc[2][1]) / 2
                    _best_dist_nm = round(
                        _runway_engine._haversine(lat, lon, _bc_lat, _bc_lon) / 1.852, 2
                    )

                # Cell colour distribution (probability thresholds)
                _n_green  = sum(1 for c in cells if c["probability"] >= 0.60)
                _n_yellow = sum(1 for c in cells if 0.35 <= c["probability"] < 0.60)
                _n_red    = sum(1 for c in cells if c["probability"] < 0.35)

                # Top recommended option type
                _top_opt  = options[0]["type"] if options else None

                # Visibility: weather engine stores metres; log in statute miles
                _vis_m    = weather.get("visibility_m")
                _vis_sm   = round(_vis_m / 1609.34, 2) if _vis_m is not None else None

                log_entry({
                    # ── Position & identity ──────────────────────────────────
                    "ts":              time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "session":         sid,
                    "callsign":        state.get("callsign"),
                    "icao24":          state.get("icao24"),
                    "lat":             lat,
                    "lon":             lon,
                    # ── Aircraft state ───────────────────────────────────────
                    "alt_ft":          state.get("altitude_ft"),   # fix: was "altitude"
                    "speed_kts":       state.get("speed_kts"),     # fix: was "speed"
                    "heading_deg":     state.get("heading_deg"),
                    "vs_fpm":          state.get("vs_fpm"),
                    # ── Risk & probability ───────────────────────────────────
                    "flight_state":    risk.get("flight_state"),
                    "risk_level":      risk.get("level"),          # fix: was "risk_level"
                    "prob_success":    prob.get("success"),
                    # ── Decision quality ─────────────────────────────────────
                    "best_cell_prob":     _best_prob,
                    "best_cell_color":    _best_color,
                    "best_cell_dist_nm":  _best_dist_nm,
                    "n_green_cells":      _n_green,
                    "n_yellow_cells":     _n_yellow,
                    "n_red_cells":        _n_red,
                    "top_option":         _top_opt,
                    # ── Weather ──────────────────────────────────────────────
                    "wx_source":       weather.get("source"),
                    "wx_confidence":   weather.get("confidence"),
                    "wx_ceiling_ft":   weather.get("ceiling_ft"),
                    "wx_wind_kts":     weather.get("wind_speed_kts"),   # fixed key
                    "wx_wind_dir_deg": weather.get("wind_direction_deg"),
                    "wx_visibility_sm":_vis_sm,
                    # ── Terrain & data provenance ────────────────────────────
                    "terrain_live":    terrain.get("elevation_live"),
                    # ── System health ────────────────────────────────────────
                    "tick_ms":         _tick_ms,
                    "n_runways_near":  len(runways),
                    "crowd_ready":     crowd_grid is not None,
                    "runway_ready":    bool(runways),
                })
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
