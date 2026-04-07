def compute_guidance(state: dict, terrain: dict) -> dict:
    altitude = state.get("altitude_ft", 5000)
    speed = state.get("speed_kts", 100)
    heading = state.get("heading_deg", 90)
    elevation_m = terrain.get("elevation_m", 300)
    slope = terrain.get("slope_deg", 0)

    elevation_ft = elevation_m * 3.28084
    agl = max(0, altitude - elevation_ft)

    descent_rate_fpm = 500
    time_to_ground_min = agl / descent_rate_fpm if descent_rate_fpm > 0 else 0

    recommended_speed = max(60, min(120, speed * 0.85))
    safe_heading = heading

    if slope > 10:
        action = "AVOID area — steep terrain. Turn 45° and seek flat ground."
    elif agl < 500:
        action = "LOW ALTITUDE — initiate landing approach immediately."
    elif agl < 2000:
        action = "Prepare for emergency landing. Identify suitable zone."
    else:
        action = "Maintain heading. Monitor terrain ahead."

    return {
        "action": action,
        "recommended_speed_kts": round(recommended_speed, 1),
        "safe_heading_deg": safe_heading,
        "agl_ft": round(agl, 0),
        "time_to_ground_min": round(time_to_ground_min, 2)
    }
