"""
SETL Crowd Engine — OSM Overpass amenity + obstacle density grid.

Queries OpenStreetMap Overpass for two node classes within the 9×9 risk-grid
bounding box:
  • amenity=*  (restaurants, schools, hospitals, markets…) → crowd density proxy
  • man_made/power tower nodes (pylons, masts, chimneys)  → obstacle density

Returns (crowd_grid, obstacle_grid) as 9×9 numpy arrays in [0, 1].
Cached at ~5 km position resolution — Overpass is only called when the
aircraft moves significantly.  Returns (None, None) on any API failure so
callers fall back gracefully to 0.0.

These values feed the TOPSIS columns in decision_engine.py:
  crowd:    AHP weight 15 % (land + water)
  obstacle: AHP weight  5 % (land)
"""

import httpx
import numpy as np

_CROWD_CACHE: dict = {"key": None, "crowd": None, "obstacle": None}
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

_CROWD_MAX    = 50   # amenity nodes per cell for saturation (crowd = 1.0)
_OBSTACLE_MAX =  5   # tower/mast nodes per cell for saturation (obstacle = 1.0)


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
    Aligned with the cell layout used by generate_cells() in app.py.
    Returns (None, None) on Overpass failure.
    """
    key = _grid_cache_key(lat, lon, cell_size)
    if _CROWD_CACHE["key"] == key and _CROWD_CACHE["crowd"] is not None:
        return _CROWD_CACHE["crowd"], _CROWD_CACHE["obstacle"]

    dim = 2 * steps + 1  # 9

    half      = cell_size * 0.5
    lat_min   = round(lat - steps * cell_size - half, 6)
    lat_max   = round(lat + steps * cell_size + half, 6)
    lon_min   = round(lon - steps * cell_size - half, 6)
    lon_max   = round(lon + steps * cell_size + half, 6)

    query = (
        f'[out:json][timeout:10];'
        f'('
        f'node[amenity]({lat_min},{lon_min},{lat_max},{lon_max});'
        f'node[man_made~"^(tower|mast|chimney|antenna)$"]({lat_min},{lon_min},{lat_max},{lon_max});'
        f'node[power="tower"]({lat_min},{lon_min},{lat_max},{lon_max});'
        f');'
        f'out body;'
    )

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp     = await client.post(
                _OVERPASS_URL,
                content=query,
                headers={"Content-Type": "text/plain"},
            )
            elements = resp.json().get("elements", [])
    except Exception:
        _CROWD_CACHE.update({"key": key, "crowd": None, "obstacle": None})
        return None, None

    crowd_counts    = np.zeros((dim, dim), dtype=float)
    obstacle_counts = np.zeros((dim, dim), dtype=float)

    for el in elements:
        elat = el.get("lat")
        elon = el.get("lon")
        if elat is None or elon is None:
            continue

        gi = int(round((elat - lat) / cell_size)) + steps
        gj = int(round((elon - lon) / cell_size)) + steps
        if not (0 <= gi < dim and 0 <= gj < dim):
            continue

        tags = el.get("tags", {})
        if "amenity" in tags:
            crowd_counts[gi, gj] += 1.0
        if "man_made" in tags or "power" in tags:
            obstacle_counts[gi, gj] += 1.0

    crowd_grid    = np.minimum(crowd_counts    / _CROWD_MAX,    1.0)
    obstacle_grid = np.minimum(obstacle_counts / _OBSTACLE_MAX, 1.0)

    _CROWD_CACHE.update({"key": key, "crowd": crowd_grid, "obstacle": obstacle_grid})
    return crowd_grid, obstacle_grid
