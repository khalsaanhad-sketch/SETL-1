import httpx
import random

async def get_terrain(lat: float, lon: float) -> dict:
    try:
        url = f"https://api.opentopodata.org/v1/srtm30m?locations={lat},{lon}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            data = resp.json()
            elevation = data["results"][0]["elevation"]
    except Exception:
        elevation = random.uniform(200, 1500)

    slope = random.uniform(0, 15)
    surface = random.choice(["flat", "hilly", "mountainous"])

    return {
        "elevation_m": round(elevation, 1),
        "slope_deg": round(slope, 2),
        "surface_type": surface
    }
