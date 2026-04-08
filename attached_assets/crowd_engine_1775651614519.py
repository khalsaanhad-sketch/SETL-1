import httpx
from cloud_app.services.population_engine import get_cached_population, sample_population

GOOGLE_API_KEY = "YOUR_GOOGLE_API_KEY"


async def fetch_places_density(lat, lon):
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"

    params = {
        "location": f"{lat},{lon}",
        "radius": 1000,
        "type": "restaurant",
        "key": GOOGLE_API_KEY
    }

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            res = await client.get(url, params=params)
            data = res.json()
            return min(len(data.get("results", [])) / 50, 1)
    except:
        return 0


async def get_crowd_density(lat, lon):
    pop_grid, window = get_cached_population(lat, lon)

    population = sample_population(lat, lon, window, pop_grid)
    places = await fetch_places_density(lat, lon)

    return min(0.7 * population + 0.3 * places, 1)