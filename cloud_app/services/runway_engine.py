"""
SETL Runway Engine

Priority order for runway data:
  1. OurAirports global CSV database (~40 k runways, loaded once per session)
  2. Overpass OSM API (fallback — cached by position key)
  3. [] — no bonus applied, grid unchanged

apply_runway_bonus() is a post-TOPSIS step that gives cells near known runways
a small probability lift (≤ 0.05).  AHP weights, TOPSIS, and logistic regression
are completely untouched.
"""

import csv
import io
import math

import httpx


# ── OurAirports global state ───────────────────────────────────────────────────
_OA_DB:      list | None = None   # None = not loaded; [] = load attempted but failed
_OA_LOADING: bool        = False   # guard against concurrent downloads


# ── Overpass position cache ────────────────────────────────────────────────────
_OVERPASS_CACHE: dict = {"key": None, "runways": None}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    R    = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a    = (math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _overpass_cache_key(lat: float, lon: float) -> tuple:
    """Quantise to ~0.1° (~11 km) for Overpass cache invalidation."""
    return (round(lat, 1), round(lon, 1))


# ── OurAirports CSV loader ─────────────────────────────────────────────────────

async def _load_ourairports() -> list:
    """
    Download and parse OurAirports runways.csv (~2 MB).
    Called once per server session; result stored in _OA_DB.

    Runway centre is the midpoint of the lower-end and higher-end coordinates.
    length_m is converted from the length_ft column.
    """
    url = "https://ourairports.com/data/runways.csv"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers={"User-Agent": "SETL-EFB/1.0"})
        text = resp.text

    reader  = csv.DictReader(io.StringIO(text))
    runways = []
    for row in reader:
        try:
            le_lat = float(row.get("le_latitude_deg")  or "nan")
            le_lon = float(row.get("le_longitude_deg") or "nan")
            he_lat = float(row.get("he_latitude_deg")  or "nan")
            he_lon = float(row.get("he_longitude_deg") or "nan")
        except (ValueError, KeyError):
            continue
        if any(math.isnan(v) for v in (le_lat, le_lon, he_lat, he_lon)):
            continue
        try:
            length_ft = float(row.get("length_ft") or 0)
        except ValueError:
            length_ft = 0.0
        runways.append({
            "ident":    row.get("airport_ident", ""),
            "lat":      (le_lat + he_lat) / 2,
            "lon":      (le_lon + he_lon) / 2,
            "length_m": round(length_ft * 0.3048),
            "surface":  row.get("surface", "unknown"),
            "source":   "ourairports",
        })
    return runways


# ── Overpass fallback ──────────────────────────────────────────────────────────

async def _fetch_overpass(lat: float, lon: float, radius_km: int) -> list:
    """
    Query Overpass for aeroway=runway ways within radius_km.
    Result cached by quantised position — Overpass is only re-queried when
    the aircraft moves more than ~11 km.
    """
    key = _overpass_cache_key(lat, lon)
    if _OVERPASS_CACHE["key"] == key and _OVERPASS_CACHE["runways"] is not None:
        return _OVERPASS_CACHE["runways"]

    query = (
        f"[out:json][timeout:12];"
        f"way[aeroway=runway](around:{radius_km * 1000},{lat:.4f},{lon:.4f});"
        f"out center tags;"
    )
    try:
        async with httpx.AsyncClient(timeout=13.0) as client:
            resp     = await client.post(
                "https://overpass-api.de/api/interpreter",
                data={"data": query},
                headers={"User-Agent": "SETL-EFB/1.0"},
            )
            elements = resp.json().get("elements", [])

        runways = []
        for el in elements:
            center = el.get("center") or {}
            tags   = el.get("tags",   {})
            rlat, rlon = center.get("lat"), center.get("lon")
            if rlat is None or rlon is None:
                continue
            try:
                length_m = float(tags.get("length") or 0)
            except ValueError:
                length_m = 0.0
            runways.append({
                "ident":    tags.get("ref", ""),
                "lat":      rlat,
                "lon":      rlon,
                "length_m": length_m,
                "surface":  tags.get("surface", "unknown"),
                "source":   "overpass",
            })
        _OVERPASS_CACHE.update({"key": key, "runways": runways})
        return runways

    except Exception:
        _OVERPASS_CACHE.update({"key": key, "runways": []})
        return []


# ── Public async API ───────────────────────────────────────────────────────────

async def get_nearby_runways(lat: float, lon: float, radius_km: int = 80) -> list:
    """
    Fetch runway data for (lat, lon).  Designed to be called as a background
    asyncio.create_task() — never blocks the WebSocket tick.

    Priority:
      1. OurAirports CSV (loaded once, then instant in-memory filter)
      2. Overpass OSM   (position-cached fallback)
      3. []             — apply_runway_bonus() is a no-op
    """
    global _OA_DB, _OA_LOADING

    # ── Load OurAirports CSV on first call ────────────────────────────────────
    if _OA_DB is None:
        try:
            _OA_DB = await _load_ourairports()
        except Exception:
            _OA_DB = []    # treat download failure as empty → use Overpass
        finally:
            _OA_LOADING = False

    # ── Filter in-memory (instant) ────────────────────────────────────────────
    if _OA_DB:
        nearby = [r for r in _OA_DB
                  if _haversine(lat, lon, r["lat"], r["lon"]) <= radius_km]
        if nearby:
            return nearby

    # ── Overpass fallback (cached) ────────────────────────────────────────────
    return await _fetch_overpass(lat, lon, radius_km)


# ── Public sync cache-read ─────────────────────────────────────────────────────

def get_cached_runways(lat: float, lon: float, radius_km: int = 80) -> list:
    """
    Instant synchronous read from whichever cache is populated.
    Never awaits or makes network calls — safe to call on every WS tick.
    Returns [] while data is still loading (no bonus applied that tick).
    """
    if _OA_DB:
        nearby = [r for r in _OA_DB
                  if _haversine(lat, lon, r["lat"], r["lon"]) <= radius_km]
        if nearby:
            return nearby

    key = _overpass_cache_key(lat, lon)
    if _OVERPASS_CACHE["key"] == key and _OVERPASS_CACHE["runways"] is not None:
        return _OVERPASS_CACHE["runways"]

    return []


# ── Post-TOPSIS runway proximity bonus ────────────────────────────────────────

_LAND_COLORS = [
    (0.25, "#2cb64f"),   # green  ≤ 0.25
    (0.45, "#d8d62b"),   # yellow ≤ 0.45
    (0.60, "#ff9c00"),   # amber  ≤ 0.60
    (1.01, "#ba2627"),   # red    > 0.60
]


def _land_color(risk: float) -> str:
    for threshold, color in _LAND_COLORS:
        if risk <= threshold:
            return color
    return "#ba2627"


def apply_runway_bonus(cells: list, runways: list) -> list:
    """
    Post-TOPSIS step: boost probability of land cells near a known runway.

    Rules:
      - Only land cells (is_water=False) are eligible.
      - Maximum bonus: +0.05 (5 percentage points) at distance 0 km.
      - Bonus tapers linearly to 0 at 10 km — cannot reach +0.05 exactly
        unless the cell is centred on the runway, which never happens.
      - A cell at risk=0.80 becomes risk=0.75 at most — still deep red.
      - Cell color is recomputed after the bonus.
      - AHP weights, TOPSIS matrix, and logistic regression are untouched.
    """
    if not runways or not cells:
        return cells

    for cell in cells:
        if cell.get("is_water"):
            continue
        corners = cell.get("corners", [])
        if len(corners) < 3:
            continue
        clat = (corners[0][0] + corners[2][0]) / 2
        clon = (corners[0][1] + corners[2][1]) / 2

        min_dist = min(
            _haversine(clat, clon, r["lat"], r["lon"]) for r in runways
        )
        if min_dist >= 10.0:
            continue

        bonus    = 0.05 * (10.0 - min_dist) / 10.0
        old_prob = cell.get("probability", 0.5)
        new_prob = min(1.0, old_prob + bonus)
        new_risk = round(1.0 - new_prob, 3)
        cell["probability"] = round(new_prob, 3)
        cell["risk"]        = new_risk
        cell["color"]       = _land_color(new_risk)

    return cells
