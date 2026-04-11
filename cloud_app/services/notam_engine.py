"""
SETL NOTAM Engine
Fetches active NOTAMs for airports within glide range using the free
aviationweather.gov API (same NOAA host as METAR and SIGMET).
Used to down-score runways that are NOTAMed CLOSED or contaminated.
"""
import time
import httpx

_NOTAM_URL = "https://aviationweather.gov/api/data/notam?format=json"
_NOTAM_CACHE: dict = {"key": None, "ts": 0.0, "closed": set(), "contaminated": set()}
_CACHE_TTL = 600

_CLOSED_KEYWORDS    = ("CLSD", "CLOSED", "U/S", "UNSERVICEABLE", "NOT AVBL")
_CONTAMINATED_KEYS  = ("CONTAMINATED", "SNOWBANK", "ICE", "WET SNOW", "SLUSH",
                        "NIL BRAKING", "POOR BRAKING", "FRICTION")


def _cache_key(lat: float, lon: float) -> str:
    return f"{round(lat, 1)},{round(lon, 1)}"


async def get_notam_advisories(lat: float, lon: float,
                                radius_deg: float = 0.8) -> dict:
    key = _cache_key(lat, lon)
    now = time.monotonic()

    if (_NOTAM_CACHE["key"] == key and
            now - _NOTAM_CACHE["ts"] < _CACHE_TTL):
        return {
            "closed":       _NOTAM_CACHE["closed"],
            "contaminated": _NOTAM_CACHE["contaminated"],
        }

    closed, contaminated = set(), set()
    try:
        bbox = (f"{lon - radius_deg:.3f},{lat - radius_deg:.3f},"
                f"{lon + radius_deg:.3f},{lat + radius_deg:.3f}")
        url = f"{_NOTAM_URL}&bbox={bbox}"
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(url, headers={"User-Agent": "SETL-EFB/1.0"})
            data = resp.json()

        notams = data if isinstance(data, list) else []
        for n in notams:
            icao = (n.get("icaoLocation") or n.get("location") or "").strip().upper()
            text = (n.get("traditionalMessage") or n.get("message") or "").upper()
            if not icao or not text:
                continue
            if any(kw in text for kw in _CLOSED_KEYWORDS):
                closed.add(icao)
            if any(kw in text for kw in _CONTAMINATED_KEYS):
                contaminated.add(icao)

    except Exception:
        pass

    _NOTAM_CACHE.update({
        "key": key, "ts": now,
        "closed": closed, "contaminated": contaminated,
    })
    return {"closed": closed, "contaminated": contaminated}


def notam_runway_penalty(runway: dict, notams: dict) -> float:
    if not notams:
        return 0.0
    icao = (runway.get("airport_ident") or runway.get("ident") or "").strip().upper()
    if not icao:
        return 0.0
    if icao in notams.get("closed", set()):
        return 0.20
    if icao in notams.get("contaminated", set()):
        return 0.10
    return 0.0
