"""
SETL SIGMET + PIREP Engine
APIs: aviationweather.gov (same NOAA host as existing METAR — no key needed)
"""
import math
import time

import httpx

_SIGMET_URL = "https://aviationweather.gov/api/data/sigmet?format=json"
_PIREP_URL  = "https://aviationweather.gov/api/data/pirep?format=json&distance=200&age=2&lat={lat}&lon={lon}"

_SIGMET_CACHE: dict = {"ts": 0.0, "sigmets": []}
_PIREP_CACHE:  dict = {"key": None, "pireps": []}


def _point_in_polygon(lat: float, lon: float, coords: list) -> bool:
    n, inside = len(coords), False
    j = n - 1
    for i in range(n):
        xi, yi = coords[i][0], coords[i][1]
        xj, yj = coords[j][0], coords[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj-xi)*(lat-yi)/(yj-yi+1e-9)+xi):
            inside = not inside
        j = i
    return inside


async def get_active_sigmets(lat: float, lon: float,
                              altitude_ft: float = 5000) -> list:
    now = time.monotonic()
    if now - _SIGMET_CACHE["ts"] < 300:
        sigmets = _SIGMET_CACHE["sigmets"]
    else:
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp    = await client.get(_SIGMET_URL,
                                           headers={"User-Agent":"SETL-EFB/1.0"})
                data    = resp.json()
                sigmets = data if isinstance(data, list) else []
                _SIGMET_CACHE.update({"ts": now, "sigmets": sigmets})
        except Exception:
            sigmets = _SIGMET_CACHE.get("sigmets", [])

    active = []
    for s in sigmets:
        try:
            alt_lo = float(s.get("altLow1") or 0) * 100
            alt_hi = float(s.get("altHi1")  or 99999) * 100
            if not (alt_lo <= altitude_ft <= alt_hi):
                continue
            coords = s.get("coords") or []
            if coords and _point_in_polygon(lat, lon, coords):
                active.append({
                    "hazard":    s.get("hazard", "UNKNOWN"),
                    "qualifier": s.get("qualifier",""),
                    "alt_lo_ft": int(alt_lo),
                    "alt_hi_ft": int(alt_hi),
                    "series":    s.get("seriesId",""),
                })
        except Exception:
            continue
    return active


def _pirep_cache_key(lat: float, lon: float) -> str:
    return f"{round(lat,1)},{round(lon,1)}"


async def get_nearby_pireps(lat: float, lon: float) -> list:
    key = _pirep_cache_key(lat, lon)
    if _PIREP_CACHE["key"] == key:
        return _PIREP_CACHE["pireps"]
    try:
        url = _PIREP_URL.format(lat=round(lat,4), lon=round(lon,4))
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(url, headers={"User-Agent":"SETL-EFB/1.0"})
            data = resp.json()
            pireps = data if isinstance(data, list) else []
        relevant = []
        for p in pireps[:10]:
            hazards = []
            if p.get("icg"):  hazards.append(f"ICE:{p['icg']}")
            if p.get("turb"): hazards.append(f"TURB:{p['turb']}")
            if p.get("wx"):   hazards.append(f"WX:{p['wx']}")
            if hazards:
                relevant.append({
                    "lat":     p.get("lat"),
                    "lon":     p.get("lon"),
                    "alt_ft":  (p.get("altLo") or 0) * 100,
                    "hazards": hazards,
                    "raw":     p.get("rawOb","")[:80],
                })
        _PIREP_CACHE.update({"key": key, "pireps": relevant})
        return relevant
    except Exception:
        _PIREP_CACHE.update({"key": key, "pireps": []})
        return []


def sigmet_risk_penalty(sigmets: list) -> float:
    if not sigmets:
        return 0.0
    penalty = 0.0
    for s in sigmets:
        h = (s.get("hazard","") or "").upper()
        q = (s.get("qualifier","") or "").upper()
        if "TS" in h or "CONVECTIVE" in h:
            penalty += 0.20
        elif "ICE" in h or "ICING" in h:
            penalty += 0.12 if "SEV" in q else 0.07
        elif "TURB" in h:
            penalty += 0.10 if "SEV" in q else 0.05
        elif "VA" in h:
            penalty += 0.25
    return round(min(0.30, penalty), 3)
