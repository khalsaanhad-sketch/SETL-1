"""
SETL Glide Envelope Engine
Computes glide reachability for each cell based on aircraft type,
altitude AGL, glide ratio, and per-cell wind component.
No external API required — pure computation.
"""
import math

GLIDE_DB = {
    "C172":(9.0,65,40),  "C182":(9.5,70,42),  "C208":(10.0,95,60),
    "PA28":(9.0,63,40),  "PA44":(9.5,80,55),  "C152":(8.5,60,35),
    "C25A":(14.0,160,85),"C25B":(14.5,165,88),"C25C":(15.0,170,90),
    "C56X":(15.0,185,95),"C680":(15.0,185,95),
    "B190":(12.0,120,70),"DH8A":(13.0,130,75),"DH8D":(14.0,145,78),
    "AT72":(15.0,140,80),"E120":(12.0,120,68),"SF34":(13.0,125,72),
    "B737":(17.0,210,120),"B738":(17.0,215,122),"B739":(17.2,218,124),
    "B38M":(17.3,215,122),"B39M":(17.3,218,124),
    "A318":(17.5,210,115),"A319":(17.5,215,118),"A320":(17.5,215,118),
    "A20N":(17.5,215,118),"A321":(17.3,220,122),"A21N":(17.3,220,122),
    "E170":(15.5,185,100),"E175":(16.0,190,105),"E190":(16.5,200,108),
    "CRJ2":(15.5,185,100),"CRJ7":(15.8,188,102),"CRJ9":(16.0,192,105),
    "B744":(17.5,255,145),"B748":(18.0,260,150),
    "B772":(19.0,250,140),"B77W":(19.0,250,140),"B77L":(19.0,250,140),
    "B788":(20.0,245,135),"B789":(20.0,245,135),"B78X":(20.0,248,137),
    "A332":(18.5,245,132),"A333":(18.5,245,132),
    "A343":(18.0,248,132),"A345":(18.0,250,134),"A346":(18.0,252,135),
    "A359":(19.5,248,130),"A35K":(19.5,250,132),
    "A388":(17.5,265,155),"A380":(17.5,265,155),
    "DEFAULT":(12.0,150,80),
}

def get_glide_params(aircraft_type: str) -> tuple:
    key = (aircraft_type or "").upper().strip()[:4]
    return GLIDE_DB.get(key, GLIDE_DB["DEFAULT"])

def compute_headwind(wind_speed_kts: float, wind_dir_deg: float,
                     heading_deg: float) -> float:
    angle = math.radians((wind_dir_deg - heading_deg + 180) % 360 - 180)
    return round(wind_speed_kts * math.cos(angle), 1)

def compute_glide_range_nm(altitude_ft: float, glide_ratio: float,
                            headwind_kts: float = 0.0,
                            best_glide_kts: float = 150.0) -> float:
    alt_nm      = max(0, altitude_ft) / 6076.12
    base_range  = alt_nm * glide_ratio
    raw_factor  = (best_glide_kts - headwind_kts) / max(best_glide_kts, 1.0)
    wind_factor = max(0.05, raw_factor)
    return round(base_range * wind_factor, 2)

def apply_glide_mask(cells: list, ac_lat: float, ac_lon: float,
                     altitude_ft: float, glide_ratio: float,
                     headwind_kts: float = 0.0,
                     best_glide_kts: float = 150.0,
                     wind_speed_kts: float = 0.0,
                     wind_dir_deg: float = 0.0) -> list:
    _STEEP_DESCENT_RATIO = 3.0
    R = 6371.0
    for cell in cells:
        c = cell.get("corners", [])
        if len(c) < 3:
            cell["reachable"]       = True
            cell["glide_margin_nm"] = 99.0
            continue
        clat = (c[0][0] + c[2][0]) / 2
        clon = (c[0][1] + c[2][1]) / 2

        dlat = math.radians(clat - ac_lat)
        dlon = math.radians(clon - ac_lon)
        a    = (math.sin(dlat/2)**2 +
                math.cos(math.radians(ac_lat)) *
                math.cos(math.radians(clat)) *
                math.sin(dlon/2)**2)
        dist_nm = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)) / 1.852

        y = math.sin(math.radians(clon - ac_lon)) * math.cos(math.radians(clat))
        x = (math.cos(math.radians(ac_lat)) * math.sin(math.radians(clat)) -
             math.sin(math.radians(ac_lat)) * math.cos(math.radians(clat)) *
             math.cos(math.radians(clon - ac_lon)))
        bearing_to_cell = (math.degrees(math.atan2(y, x)) + 360) % 360

        cell_headwind = compute_headwind(wind_speed_kts, wind_dir_deg, bearing_to_cell)

        max_nm = compute_glide_range_nm(altitude_ft, glide_ratio,
                                        cell_headwind, best_glide_kts)
        agl_ft = max(0, cell.get("clearance_ft", altitude_ft))
        min_dist_nm = agl_ft * _STEEP_DESCENT_RATIO / 6076.12
        margin  = round(max_nm - dist_nm, 2)
        reachable = dist_nm <= max_nm and dist_nm >= min_dist_nm
        cell["reachable"]       = reachable
        cell["glide_margin_nm"] = margin
    return cells

def compute_reachability_stats(cells: list) -> dict:
    reachable      = [c for c in cells if c.get("reachable", True)]
    land_reachable = [c for c in reachable if not c.get("is_water", False)]
    green_reachable= [c for c in land_reachable if c.get("probability",0)>=0.60]
    return {
        "total_cells":      len(cells),
        "reachable_cells":  len(reachable),
        "land_reachable":   len(land_reachable),
        "green_reachable":  len(green_reachable),
    }
