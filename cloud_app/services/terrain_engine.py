import httpx


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

    elevation = None

    # ── Single DEM call: etopo1 (global, land + ocean) ──────────────────────
    try:
        url = f"https://api.opentopodata.org/v1/etopo1?locations={lat},{lon}"
        async with httpx.AsyncClient(timeout=7.0) as client:
            resp = await client.get(url)
            data = resp.json()
            val  = data.get("results", [{}])[0].get("elevation")
            if val is not None:
                elevation = float(val)
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
        slope_deg      = round(max(0.5, elevation / 500 * 3), 2)
        landing_viable = True
    elif elevation < 500:
        surface_type   = "hilly"
        slope_deg      = round(3 + elevation / 500 * 7, 2)
        landing_viable = True
    elif elevation < 1500:
        surface_type   = "mountainous"
        slope_deg      = round(10 + (elevation - 500) / 1000 * 15, 2)
        landing_viable = True
    else:
        surface_type   = "high_mountain"
        slope_deg      = round(min(40, 25 + (elevation - 1500) / 1000 * 10), 2)
        landing_viable = False      # Extreme altitude

    return {
        "elevation_m":    round(elevation, 1),
        "slope_deg":      slope_deg,
        "surface_type":   surface_type,
        "is_water":       is_water,
        "landing_viable": landing_viable,
    }
