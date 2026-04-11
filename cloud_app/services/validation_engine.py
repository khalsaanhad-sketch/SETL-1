"""
SETL Validation Engine — Retrospective analytics from flight logs.
No external API — reads local CSV only.
"""
import csv
from pathlib import Path
from collections import defaultdict

LOG_FILE = Path("logs/flight_logs.csv")


def load_logs() -> list:
    if not LOG_FILE.exists():
        return []
    try:
        with LOG_FILE.open(encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def compute_analytics(rows: list) -> dict:
    if not rows:
        return {"total_records": 0}
    total = len(rows)
    risk_dist, wx_src = defaultdict(int), defaultdict(int)
    ticks, probs, greens = [], [], []
    sessions, types = set(), defaultdict(int)
    for r in rows:
        risk_dist[r.get("risk_level","?")] += 1
        wx_src[r.get("wx_source","?")] += 1
        sessions.add(r.get("session",""))
        types[r.get("aircraft_type","UNKNOWN")] += 1
        try: ticks.append(int(r.get("tick_ms",0) or 0))
        except Exception: pass
        try: probs.append(float(r.get("prob_success",0) or 0))
        except Exception: pass
        try: greens.append(int(r.get("n_green_cells",0) or 0))
        except Exception: pass
    def pct(lst, p):
        s = sorted(lst)
        return s[min(int(len(s)*p/100), len(s)-1)] if s else 0
    return {
        "total_records":      total,
        "unique_sessions":    len(sessions),
        "risk_distribution":  dict(risk_dist),
        "wx_source_breakdown":dict(wx_src),
        "top_aircraft_types": dict(sorted(types.items(),key=lambda x:-x[1])[:8]),
        "tick_ms_p50":        pct(ticks, 50),
        "tick_ms_p95":        pct(ticks, 95),
        "mean_success_prob":  round(sum(probs)/len(probs),3) if probs else 0,
        "mean_green_cells":   round(sum(greens)/len(greens),1) if greens else 0,
        "pct_terrain_live":   round(sum(1 for r in rows if r.get("terrain_live")=="True")/total*100,1),
        "pct_metar":          round(wx_src.get("metar",0)/total*100,1),
        "critical_events":    risk_dist.get("CRITICAL",0),
        "high_events":        risk_dist.get("HIGH",0),
    }


def detect_log_anomalies(rows: list, window: int = 15) -> list:
    recent = rows[-window:] if len(rows) >= window else rows
    flags  = []
    try:
        slow     = sum(1 for r in recent if int(r.get("tick_ms",0) or 0) > 5000)
        no_dem   = sum(1 for r in recent if r.get("terrain_live") == "False")
        no_green = sum(1 for r in recent if r.get("n_green_cells") == "0")
        if slow     > 2:  flags.append(f"HIGH_LATENCY: {slow}/{len(recent)} ticks >5s")
        if no_dem   > 3:  flags.append(f"DEM_API_DOWN: {no_dem}/{len(recent)} ticks no live terrain")
        if no_green > 5:  flags.append(f"NO_SAFE_LZ: {no_green}/{len(recent)} ticks zero green cells")
    except Exception:
        pass
    return flags
