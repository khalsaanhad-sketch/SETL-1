"""
SETL Crowd Engine — OSM Overpass multi-signal crowd + obstacle density grid.

Population density proxy uses TWO complementary OSM signals:

  1. node[amenity=*]
       Captures commercial / civic activity zones (restaurants, schools,
       hospitals, offices, markets…).  Fast — only node elements.

  2. way[landuse=residential|housing]
       Captures RESIDENTIAL zones — housing estates, suburbs, villas, HDB
       blocks.  Returns tens to a few hundred polygon centroids per grid area,
       NOT tens-of-thousands of individual building footprints.

       Why landuse instead of way[building=residential]:
       In well-mapped cities (Tokyo, Singapore, Seoul) individual building ways
       number in the hundreds of thousands per 9×9 grid, causing Overpass
       queries to return 100k+ elements and stall the WS tick.
       landuse=residential polygons are orders-of-magnitude fewer (typically
       10–500 per grid) while still correctly identifying "this area is where
       people live."

crowd_score per cell = max(amenity_score, residential_zone_score)
  → Commercial zones score high from amenities
  → Residential-only zones score high from landuse coverage
  → Rural/agricultural fields score ≈ 0 from both signals  ✓

obstacle=*  (aviation-relevant structures: power towers, masts, chimneys)
  → 5 % TOPSIS weight in decision_engine.py

Returns (crowd_grid, obstacle_grid) as 9×9 numpy arrays in [0, 1].
Cached at ~5 km position resolution.  Returns (None, None) on any API failure
so generate_cells() falls back gracefully to crowd = 0.0 / obstacle = 0.0.
"""

import httpx
import numpy as np

# "pending" = cache key of an in-flight Overpass request launched as a background
# asyncio.Task by the WS loop.  Prevents duplicate concurrent requests for the
# same grid position when the WS ticks faster than Overpass responds.
_CROWD_CACHE: dict = {"key": None, "crowd": None, "obstacle": None, "pending": None}
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Saturation thresholds — count at which a cell reaches score = 1.0
_AMENITY_MAX    = 40  # 40 amenity nodes / cell  → amenity_score  = 1.0
_LANDUSE_MAX    =  4  # 4 residential landuse centroids / cell → zone_score = 1.0
_OBSTACLE_MAX   =  5  # 5 tower / mast nodes / cell → obstacle_score = 1.0

# Element processing guard — bail out early if Overpass returns unexpectedly
# large responses (e.g. after query changes or server-side bugs)
_MAX_ELEMENTS = 5_000


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
    crowd_grid  = max(amenity_score, residential_landuse_score) per cell
    obstacle_grid = tower/mast/chimney density per cell
    Returns (None, None) on Overpass failure — callers fall back to 0.0.
    """
    key = _grid_cache_key(lat, lon, cell_size)
    if _CROWD_CACHE["key"] == key and _CROWD_CACHE["crowd"] is not None:
        return _CROWD_CACHE["crowd"], _CROWD_CACHE["obstacle"]

    # Mark this key as in-flight so the WS loop won't fire a second parallel request
    _CROWD_CACHE["pending"] = key

    dim  = 2 * steps + 1  # 9
    half = cell_size * 0.5

    lat_min = round(lat - steps * cell_size - half, 6)
    lat_max = round(lat + steps * cell_size + half, 6)
    lon_min = round(lon - steps * cell_size - half, 6)
    lon_max = round(lon + steps * cell_size + half, 6)
    bbox    = f"{lat_min},{lon_min},{lat_max},{lon_max}"

    # Efficient query:
    #   • amenity nodes         — commercial / civic activity
    #   • landuse=residential ways — where people LIVE (centroid only, << building ways)
    #   • obstacle nodes        — aviation hazards
    # NO [maxsize] override: Overpass default (512 MB) handles even the densest cities.
    # Using [maxsize:20MB] caused silent OOM failures in Tokyo/Singapore because
    # their amenity-node counts (10k+ per grid) push past 20 MB before Overpass
    # can stream a response.
    query = (
        f'[out:json][timeout:14];'
        f'('
        f'node[amenity]({bbox});'
        f'node[man_made~"^(tower|mast|chimney|antenna)$"]({bbox});'
        f'node[power="tower"]({bbox});'
        f'way[landuse~"^(residential|housing)$"]({bbox});'
        f');'
        f'out center;'
    )

    try:
        async with httpx.AsyncClient(timeout=16.0) as client:
            resp = await client.post(
                _OVERPASS_URL,
                content=query,
                headers={"Content-Type": "text/plain"},
            )
            data     = resp.json()
            remark   = data.get("remark", "")
            elements = data.get("elements", [])
            # Overpass signals memory/runtime errors via the "remark" field.
            # Treat any such condition as a failure so callers use the 0.0 fallback.
            if "error" in remark.lower() or "out of memory" in remark.lower():
                raise RuntimeError(f"Overpass remark: {remark[:120]}")
    except Exception:
        _CROWD_CACHE.update({"key": key, "crowd": None, "obstacle": None, "pending": None})
        return None, None

    amenity_counts  = np.zeros((dim, dim), dtype=float)
    landuse_counts  = np.zeros((dim, dim), dtype=float)
    obstacle_counts = np.zeros((dim, dim), dtype=float)

    for el in elements[:_MAX_ELEMENTS]:
        el_type = el.get("type")
        tags    = el.get("tags", {})

        # Resolve centroid: nodes have lat/lon; ways give el["center"]
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

        # ── Signal 1: amenity nodes ────────────────────────────────────────────
        if "amenity" in tags:
            amenity_counts[gi, gj] += 1.0

        # ── Signal 2: residential landuse zone centroid ────────────────────────
        if el_type == "way" and tags.get("landuse") in {"residential", "housing"}:
            landuse_counts[gi, gj] += 1.0

        # ── Obstacle: aviation-relevant structures ─────────────────────────────
        if "man_made" in tags or "power" in tags:
            obstacle_counts[gi, gj] += 1.0

    # Normalise each signal to [0, 1]
    amenity_score  = np.minimum(amenity_counts  / _AMENITY_MAX,  1.0)
    landuse_score  = np.minimum(landuse_counts  / _LANDUSE_MAX,  1.0)
    obstacle_grid  = np.minimum(obstacle_counts / _OBSTACLE_MAX, 1.0)

    # crowd = max of both signals
    # A cell is "crowded" if it has many amenities OR it is inside a residential zone
    crowd_grid = np.maximum(amenity_score, landuse_score)

    _CROWD_CACHE.update({"key": key, "crowd": crowd_grid, "obstacle": obstacle_grid, "pending": None})
    return crowd_grid, obstacle_grid
