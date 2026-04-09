"""
SETL Crowd Engine — OSM Overpass multi-signal crowd + obstacle density grid.

Population density proxy uses TWO complementary OSM signals:

  1. amenity=*  (restaurants, schools, hospitals, markets, offices…)
       → Captures commercial / public activity zones.
       → Miss: residential areas with no shops.

  2. way[building=residential|apartments|house|…]  (residential buildings)
       → Captures where people actually LIVE — housing estates, flats, villas.
       → Fills exactly the gap that WorldPop would fill over OSM-amenity alone.

crowd_score per cell = max(amenity_score, building_score)
This ensures a dense housing estate with zero amenities still scores correctly.

WorldPop REST API (api.worldpop.org/v1/services/stats) was tested but all
dataset parameter values are currently rejected with 422 — the API endpoint
appears to have changed or is broken upstream. The two-signal OSM approach
achieves the same residential-population-proxy goal without external
authentication or large raster downloads.

obstacle=*  (aviation-relevant obstacles: power towers, masts, chimneys)
       → 5 % TOPSIS weight; surfaces are not affected by crowd signal.

Returns (crowd_grid, obstacle_grid) as 9×9 numpy arrays in [0, 1].
Cached at ~5 km position resolution.  Returns (None, None) on any API failure
so callers fall back gracefully to 0.0.
"""

import httpx
import numpy as np

_CROWD_CACHE: dict = {"key": None, "crowd": None, "obstacle": None}
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Saturation thresholds — count at which a cell reaches score = 1.0
_AMENITY_MAX  = 40   # 40 amenity nodes/cell  → amenity_score = 1.0
_BUILDING_MAX = 80   # 80 residential ways/cell → building_score = 1.0
_OBSTACLE_MAX =  5   # 5  tower/mast nodes/cell → obstacle_score = 1.0

# Residential building tags that imply human occupancy
_RESIDENTIAL_BUILDING_TYPES = {
    "residential", "apartments", "house", "dormitory", "bungalow",
    "semidetached_house", "terrace", "tower_block", "flat", "detached",
    "yes",   # generic 'yes' catches untagged residential in OSM
}


def _grid_cache_key(lat: float, lon: float, cell_size: float = 0.01) -> tuple:
    q = cell_size * 5
    return (round(lat / q) * q, round(lon / q) * q)


async def get_osm_crowd_grid(
    lat: float,
    lon: float,
    steps: int = 4,
    cell_size: float = 0.01,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """
    Return (crowd_grid, obstacle_grid) as (2*steps+1) × (2*steps+1) arrays.
    Aligned with the 9×9 cell layout used by generate_cells() in app.py.
    Returns (None, None) on Overpass failure — callers fall back to crowd=0.0.

    crowd_grid  = max(amenity_score, building_score)  per cell
    obstacle_grid = tower/mast/chimney density         per cell
    """
    key = _grid_cache_key(lat, lon, cell_size)
    if _CROWD_CACHE["key"] == key and _CROWD_CACHE["crowd"] is not None:
        return _CROWD_CACHE["crowd"], _CROWD_CACHE["obstacle"]

    dim  = 2 * steps + 1  # 9
    half = cell_size * 0.5

    lat_min = round(lat - steps * cell_size - half, 6)
    lat_max = round(lat + steps * cell_size + half, 6)
    lon_min = round(lon - steps * cell_size - half, 6)
    lon_max = round(lon + steps * cell_size + half, 6)
    bbox    = f"{lat_min},{lon_min},{lat_max},{lon_max}"

    # Single query: amenity nodes + residential building ways + obstacle nodes.
    # 'out center;' gives centroid lat/lon for ways; nodes already have lat/lon.
    query = (
        f'[out:json][timeout:15];'
        f'('
        f'node[amenity]({bbox});'
        f'node[man_made~"^(tower|mast|chimney|antenna)$"]({bbox});'
        f'node[power="tower"]({bbox});'
        f'way[building~"^(residential|apartments|house|dormitory|bungalow|'
        f'semidetached_house|terrace|tower_block|flat|detached|yes)$"]({bbox});'
        f');'
        f'out center;'
    )

    try:
        async with httpx.AsyncClient(timeout=16.0) as client:
            resp     = await client.post(
                _OVERPASS_URL,
                content=query,
                headers={"Content-Type": "text/plain"},
            )
            elements = resp.json().get("elements", [])
    except Exception:
        _CROWD_CACHE.update({"key": key, "crowd": None, "obstacle": None})
        return None, None

    amenity_counts  = np.zeros((dim, dim), dtype=float)
    building_counts = np.zeros((dim, dim), dtype=float)
    obstacle_counts = np.zeros((dim, dim), dtype=float)

    for el in elements:
        el_type = el.get("type")
        tags    = el.get("tags", {})

        # Resolve centroid: nodes have lat/lon directly; ways need el["center"]
        if el_type == "node":
            elat = el.get("lat")
            elon = el.get("lon")
        else:
            ctr  = el.get("center", {})
            elat = ctr.get("lat")
            elon = ctr.get("lon")

        if elat is None or elon is None:
            continue

        gi = int(round((elat - lat) / cell_size)) + steps
        gj = int(round((elon - lon) / cell_size)) + steps
        if not (0 <= gi < dim and 0 <= gj < dim):
            continue

        # ── Signal 1: amenity nodes (commercial / public activity) ────────────
        if "amenity" in tags:
            amenity_counts[gi, gj] += 1.0

        # ── Signal 2: residential building ways (where people live) ───────────
        if el_type == "way" and tags.get("building") in _RESIDENTIAL_BUILDING_TYPES:
            building_counts[gi, gj] += 1.0

        # ── Obstacle: aviation-relevant structures ─────────────────────────────
        if "man_made" in tags or "power" in tags:
            obstacle_counts[gi, gj] += 1.0

    # Normalise each signal to [0, 1]
    amenity_score  = np.minimum(amenity_counts  / _AMENITY_MAX,  1.0)
    building_score = np.minimum(building_counts / _BUILDING_MAX, 1.0)
    obstacle_grid  = np.minimum(obstacle_counts / _OBSTACLE_MAX, 1.0)

    # crowd = max of both signals: a cell is crowded if EITHER amenities OR
    # residential buildings are dense — captures both market areas and housing
    # estates that amenity-only queries would miss.
    crowd_grid = np.maximum(amenity_score, building_score)

    _CROWD_CACHE.update({"key": key, "crowd": crowd_grid, "obstacle": obstacle_grid})
    return crowd_grid, obstacle_grid
