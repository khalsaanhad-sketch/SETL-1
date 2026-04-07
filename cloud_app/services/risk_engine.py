def compute_risk(state: dict) -> dict:
    altitude = state.get("altitude_ft", 5000)
    speed = state.get("speed_kts", 100)
    heading = state.get("heading_deg", 90)

    altitude_risk = max(0.0, 1.0 - (altitude / 10000.0))
    speed_risk = min(1.0, speed / 300.0)

    overall = round((altitude_risk * 0.6 + speed_risk * 0.4), 3)

    level = "LOW"
    if overall > 0.7:
        level = "CRITICAL"
    elif overall > 0.5:
        level = "HIGH"
    elif overall > 0.3:
        level = "MODERATE"

    return {
        "overall": overall,
        "level": level,
        "altitude_risk": round(altitude_risk, 3),
        "speed_risk": round(speed_risk, 3),
        "heading_deg": heading
    }
