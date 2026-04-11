import httpx
import numpy as np


def _ocean_estimate(lat: float, lon: float) -> float:
    """
    Rough heuristic: return a plausible elevation when both DEM APIs are
    unavailable.  Checks very approximate land-mass bounding boxes; returns a
    land fallback (~300 m) for coordinates that fall inside them, or a
    mid-ocean depth (~-2500 m) for open-ocean coordinates.
    """
    land_boxes = [
        # (lat_min, lon_min, lat_max, lon_max)
        (  8,  68,  37,  97),   # Indian subcontinent
        ( 35, -10,  71,  40),   # Europe
        ( 15, -168, 72, -50),   # North America
        (-35, -18,  37,  52),   # Africa
        ( 18,  73,  55, 145),   # China / East Asia
        (-10,  95,  28, 145),   # South-East Asia
        (-55, -82,  13, -33),   # South America
        (-44, 113, -10, 154),   # Australia
        ( 55,  28,  72,  68),   # Russia / Central Asia
        ( 36,  26,  42,  45),   # Middle East / Anatolia
    ]
    for lat1, lon1, lat2, lon2 in land_boxes:
        if lat1 <= lat <= lat2 and lon1 <= lon <= lon2:
            return 300.0        # Conservative land default
    return -2500.0              # Open ocean default


async def get_terrain(lat: float, lon: float) -> dict:
    """
    Fetch terrain / bathymetric data for a coordinate using a single
    etopo1 API call (global DEM: covers land AND ocean depths).

    etopo1 returns negative values for below-sea-level (ocean) positions,
    positive for land, and None for missing data.
    """

    elevation      = None
    elevation_live = False   # True only when etopo1 API returned a real value

    # ── Single DEM call: etopo1 (global, land + ocean) ──────────────────────
    try:
        url = f"https://api.opentopodata.org/v1/etopo1?locations={lat},{lon}"
        async with httpx.AsyncClient(timeout=7.0) as client:
            resp = await client.get(url)
            data = resp.json()
            val  = data.get("results", [{}])[0].get("elevation")
            if val is not None:
                elevation      = float(val)
                elevation_live = True
    except Exception:
        pass

    # ── Fallback: coordinate-based estimate when API is unavailable ──────────
    if elevation is None:
        elevation = _ocean_estimate(lat, lon)

    # ── Classify surface type and derive slope from elevation ────────────────
    is_water = elevation <= 0

    if elevation <= -2000:
        surface_type   = "deep_ocean"
        slope_deg      = 0.3
        landing_viable = False
    elif elevation <= -200:
        surface_type   = "continental_shelf"
        slope_deg      = 1.2
        landing_viable = False
    elif elevation <= 0:
        surface_type   = "coastal_water"
        slope_deg      = 0.5
        landing_viable = False
    elif elevation < 50:
        surface_type   = "flat"
        slope_deg      = 1.5
        landing_viable = True
    elif elevation < 500:
        surface_type   = "hilly"
        slope_deg      = 2.5
        landing_viable = True
    elif elevation < 1500:
        surface_type   = "mountainous"
        slope_deg      = 4.0
        landing_viable = True
    else:
        surface_type   = "high_mountain"
        slope_deg      = 6.0
        landing_viable = False

    return {
        "elevation_m":    round(elevation, 1),
        "slope_deg":      slope_deg,
        "surface_type":   surface_type,
        "is_water":       is_water,
        "landing_viable": landing_viable,
        "elevation_live": elevation_live,
    }


# ── Terrain grid — slope & roughness per cell ─────────────────────────────────
# Fetches a 9×9 DEM grid matching the cell layout in generate_cells().
# Cached by quantised position so the batch request only fires when the
# aircraft moves more than ~5 km from the last cached centre.

_GRID_CACHE: dict = {"key": None, "slope": None, "roughness": None, "elev": None}


def _grid_cache_key(lat: float, lon: float, cell_size: float = 0.01) -> tuple:
    """Quantise to 5-cell (~0.05°) resolution for cache invalidation."""
    q = cell_size * 5
    return (round(lat / q) * q, round(lon / q) * q)


def compute_slope_grid(elevation_grid: np.ndarray, cell_size_m: float = 1111.0) -> np.ndarray:
    """
    Slope in degrees computed from a 2-D elevation grid via numpy gradient.
    cell_size_m: approximate metres per grid step (0.01° ≈ 1111 m at mid-lat).
    """
    dzdy, dzdx = np.gradient(elevation_grid, cell_size_m, cell_size_m)
    return np.degrees(np.arctan(np.sqrt(dzdx ** 2 + dzdy ** 2)))


def compute_roughness_grid(elevation_grid: np.ndarray) -> np.ndarray:
    """
    Terrain roughness: standard deviation of elevation in a 3×3 neighbourhood.
    Higher values indicate irregular / broken ground — harder to land on.
    Pure-numpy implementation using sliding_window_view (no scipy needed).
    """
    padded  = np.pad(elevation_grid, 1, mode="reflect")
    windows = np.lib.stride_tricks.sliding_window_view(padded, (3, 3))
    return windows.std(axis=(-2, -1))


async def get_terrain_grid(
    lat: float,
    lon: float,
    steps: int = 4,
    cell_size: float = 0.01,
) -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None]:
    """
    Return (slope_grid, roughness_grid) as 9×9 numpy arrays aligned with the
    cell layout used by generate_cells() (i, j in range(-steps, steps+1)).

    The batch DEM request is cached: returns instantly when the aircraft has
    not moved more than ~5 km since the last fetch.  On API failure returns
    (None, None) so callers fall back gracefully.
    """
    key = _grid_cache_key(lat, lon, cell_size)
    if _GRID_CACHE["key"] == key and _GRID_CACHE["slope"] is not None:
        return _GRID_CACHE["slope"], _GRID_CACHE["roughness"], _GRID_CACHE["elev"]

    dim = 2 * steps + 1   # 9 for steps=4

    # Build a flat list of (lat, lon) pairs in row-major order
    pairs = [
        (lat + i * cell_size, lon + j * cell_size)
        for i in range(-steps, steps + 1)
        for j in range(-steps, steps + 1)
    ]
    locations = "|".join(f"{la:.6f},{lo:.6f}" for la, lo in pairs)
    url = f"https://api.opentopodata.org/v1/etopo1?locations={locations}"

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp    = await client.get(url)
            results = resp.json().get("results", [])
            elevs   = [float(r.get("elevation") or 0.0) for r in results]

        if len(elevs) != dim * dim:
            raise ValueError(f"Expected {dim*dim} elevations, got {len(elevs)}")

        grid      = np.array(elevs, dtype=float).reshape(dim, dim)
        slope     = compute_slope_grid(grid)
        roughness = compute_roughness_grid(grid)

        _GRID_CACHE.update({"key": key, "slope": slope, "roughness": roughness, "elev": grid})
        return slope, roughness, grid

    except Exception:
        # Cache the failure at this position so we don't hammer the API
        _GRID_CACHE.update({"key": key, "slope": None, "roughness": None, "elev": None})
        return None, None, None
