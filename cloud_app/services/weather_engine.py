import httpx

async def get_weather(lat: float, lon: float) -> dict:
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current_weather=true"
            f"&hourly=visibility,precipitation"
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            data = resp.json()
            cw = data.get("current_weather", {})
            wind_speed = cw.get("windspeed", 0)
            wind_dir = cw.get("winddirection", 0)
            hourly = data.get("hourly", {})
            visibility = hourly.get("visibility", [10000])[0]
            precipitation = hourly.get("precipitation", [0])[0]
    except Exception:
        wind_speed = 10.0
        wind_dir = 270
        visibility = 10000
        precipitation = 0.0

    return {
        "wind_speed_kts": round(wind_speed * 0.539957, 1),
        "wind_direction_deg": wind_dir,
        "visibility_m": visibility,
        "precipitation_mm": precipitation
    }
