import math

def _haversine_nm(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a)) / 1.852

def _bearing(lat1, lon1, lat2, lon2):
    la1, la2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2-lon1)
    x  = math.sin(dl)*math.cos(la2)
    y  = math.cos(la1)*math.sin(la2) - math.sin(la1)*math.cos(la2)*math.cos(dl)
    return round((math.degrees(math.atan2(x,y))+360)%360, 1)

def _centre(cell):
    c = cell.get("corners",[])
    return ((c[0][0]+c[2][0])/2, (c[0][1]+c[2][1])/2) if len(c)>=3 else (None,None)

def compute_options(prob: dict, cells: list = None,
                    aircraft_lat: float = 0.0, aircraft_lon: float = 0.0,
                    aircraft_heading: float = 0.0) -> list:
    success = prob.get("success", 0.5)

    if not cells:
        return [
            {"type":"PRIMARY",   "description":"Straight-ahead emergency landing",
             "success_probability":round(success*0.95,3),"recommended":success>0.5,
             "bearing_deg":None,"distance_nm":None,"cell_lat":None,"cell_lon":None,
             "slope_deg":None,"reachable":None},
            {"type":"SECONDARY", "description":"Turn 30° right and descend",
             "success_probability":round(success*0.80,3),"recommended":0.3<success<=0.5,
             "bearing_deg":None,"distance_nm":None,"cell_lat":None,"cell_lon":None,
             "slope_deg":None,"reachable":None},
            {"type":"EMERGENCY", "description":"Immediate forced landing — best available",
             "success_probability":round(success*0.60,3),"recommended":success<=0.3,
             "bearing_deg":None,"distance_nm":None,"cell_lat":None,"cell_lon":None,
             "slope_deg":None,"reachable":None},
        ]

    land   = [c for c in cells if not c.get("is_water", False)]
    # Primary pool: reachable land cells only.
    # Safety: a cell outside the glide envelope must NEVER be PRIMARY or SECONDARY.
    reach  = [c for c in land if c.get("reachable", True)]
    # Fallback 1: all land cells (if glide mask not yet applied, or all unreachable)
    pool   = reach if reach else land
    # Fallback 2: water ditching (if no land at all)

    if not pool:
        water  = sorted(cells, key=lambda c: c.get("probability",0), reverse=True)
        best   = water[0] if water else {}
        blat,blon = _centre(best)
        brng   = _bearing(aircraft_lat,aircraft_lon,blat,blon) if blat else None
        dist   = round(_haversine_nm(aircraft_lat,aircraft_lon,blat,blon),2) if blat else None
        return [{"type":"DITCHING",
                 "description":"Controlled water ditching — gear up, flaps full",
                 "success_probability":round(best.get("probability",0.2),3),
                 "recommended":True,"bearing_deg":brng,"distance_nm":dist,
                 "cell_lat":round(blat,5) if blat else None,
                 "cell_lon":round(blon,5) if blon else None,
                 "slope_deg":0.0,"reachable":False}]

    ranked = sorted(pool, key=lambda c: c.get("probability",0), reverse=True)
    labels = [("PRIMARY","Best LZ — highest safety score"),
              ("SECONDARY","Alternative LZ — second best"),
              ("EMERGENCY","Emergency LZ — minimum safe zone")]
    options = []
    for i,(otype,base) in enumerate(labels):
        if i >= len(ranked): break
        cell = ranked[i]
        clat,clon = _centre(cell)
        p     = cell.get("probability",0)
        brng  = _bearing(aircraft_lat,aircraft_lon,clat,clon) if clat else None
        dist  = round(_haversine_nm(aircraft_lat,aircraft_lon,clat,clon),2) if clat else None
        slope = cell.get("slope",0)
        crowd = cell.get("crowd",0)
        desc  = base
        if brng  is not None: desc += f". Turn {brng}\u00b0"
        if dist  is not None: desc += f", {dist} nm"
        if slope: desc += f". Slope {slope}\u00b0"
        if crowd > 0.4: desc += ". Dense area"
        if not cell.get("reachable",True): desc += " — outside glide range"
        options.append({
            "type":otype,"description":desc,
            "success_probability":round(p,3),
            "recommended":i==0,
            "bearing_deg":brng,"distance_nm":dist,
            "cell_lat":round(clat,5) if clat else None,
            "cell_lon":round(clon,5) if clon else None,
            "slope_deg":round(slope,1),"crowd_score":round(crowd,2),
            "reachable":cell.get("reachable",True),
        })
    return options
