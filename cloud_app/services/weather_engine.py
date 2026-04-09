import httpx

_SM_TO_M = 1609.34   # statute miles → metres (METAR visibility unit)


async def _fetch_metar(lat: float, lon: float) -> dict | None:
    """
    Fetch the nearest METAR observation from NOAA aviationweather.gov.

    Uses a 2° bounding box (~220 km) to find nearby stations, then picks
    the one closest to (lat, lon) by Euclidean degree-distance.

    Returns a structured weather dict on success, None on any failure so
    the caller can fall back to Open-Meteo without raising.
    """
    bbox = f"{lon - 2:.2f},{lat - 2:.2f},{lon + 2:.2f},{lat + 2:.2f}"
    url  = (
        "https://aviationweather.gov/api/data/metar"
        f"?bbox={bbox}&format=json&hours=1"
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp   = await client.get(url, headers={"User-Agent": "SETL-EFB/1.0"})
            result = resp.json()

        if not isinstance(result, list) or not result:
            return None

        # Pick nearest station by Euclidean distance in degrees
        best = min(
            result,
            key=lambda s: (float(s.get("lat", lat)) - lat) ** 2
                        + (float(s.get("lon", lon)) - lon) ** 2,
        )

        wind_speed_kts = round(float(best.get("wspd") or 0), 1)
        wind_dir       = int(best.get("wdir") or 0)

        # visib is a string in statute miles; "+" means ≥10 SM
        try:
            visib_sm = float(str(best.get("visib", "10")).replace("+", ""))
        except (ValueError, TypeError):
            visib_sm = 10.0
        visibility_m = round(visib_sm * _SM_TO_M)

        # Ceiling: lowest BKN or OVC layer — METAR base encoded in hundreds of ft
        ceiling_ft = None
        for layer in (best.get("sky") or []):
            if layer.get("cover") in ("BKN", "OVC"):
                base = layer.get("base")
                if base is not None:
                    ceiling_ft = int(base) * 100
                    break

        return {
            "wind_speed_kts":     wind_speed_kts,
            "wind_direction_deg": wind_dir,
            "visibility_m":       visibility_m,
            "precipitation_mm":   0.0,        # METAR has no precipitation rate field
            "ceiling_ft":         ceiling_ft,
            "confidence":         "real",
            "source":             "metar",
            "station":            best.get("stationId", ""),
        }

    except Exception:
        return None


async def get_weather(lat: float, lon: float) -> dict:
    """
    Return structured weather for (lat, lon).

    Priority:
      1. Real METAR from nearest aviation weather station (NOAA) — confidence "real"
      2. Open-Meteo NWP forecast — confidence "approx"
      3. Conservative static defaults — confidence "low"

    All three paths return the same keys so downstream code never needs to
    branch on the source.
    """
    # ── Primary: real METAR ──────────────────────────────────────────────────
    metar = await _fetch_metar(lat, lon)
    if metar:
        return metar

    # ── Fallback: Open-Meteo NWP ─────────────────────────────────────────────
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

        cw            = data.get("current_weather", {})
        wind_speed    = cw.get("windspeed",    0)
        wind_dir      = cw.get("winddirection", 0)
        hourly        = data.get("hourly", {})
        visibility    = hourly.get("visibility",    [10000])[0]
        precipitation = hourly.get("precipitation", [0])[0]

        return {
            "wind_speed_kts":     round(wind_speed * 0.539957, 1),
            "wind_direction_deg": wind_dir,
            "visibility_m":       visibility,
            "precipitation_mm":   precipitation,
            "ceiling_ft":         None,
            "confidence":         "approx",
            "source":             "open-meteo",
            "station":            "",
        }

    except Exception:
        pass

    # ── Last resort: static conservative defaults ────────────────────────────
    return {
        "wind_speed_kts":     10.0,
        "wind_direction_deg": 270,
        "visibility_m":       10000,
        "precipitation_mm":   0.0,
        "ceiling_ft":         None,
        "confidence":         "low",
        "source":             "default",
        "station":            "",
    }
