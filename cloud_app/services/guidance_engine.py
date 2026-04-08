def compute_guidance(state: dict, terrain: dict, weather: dict | None = None) -> dict:
    altitude    = state.get("altitude_ft", 5000)
    speed       = state.get("speed_kts", 100)
    heading     = state.get("heading_deg", 90)
    elevation_m = terrain.get("elevation_m", 300)
    slope       = terrain.get("slope_deg", 0)

    elevation_ft       = elevation_m * 3.28084
    agl                = max(0, altitude - elevation_ft)
    descent_rate_fpm   = 500
    time_to_ground_min = agl / descent_rate_fpm if descent_rate_fpm > 0 else 0
    recommended_speed  = max(60, min(120, speed * 0.85))

    # ── Wind-aware heading ────────────────────────────────────────────────────
    wind_kts  = 0.0
    wind_dir  = heading          # default: keep current heading
    vis_m     = 10000.0
    precip_mm = 0.0

    if weather:
        wind_kts  = weather.get("wind_speed_kts",    0.0)
        wind_dir  = weather.get("wind_direction_deg", heading)
        vis_m     = weather.get("visibility_m",     10000.0)
        precip_mm = weather.get("precipitation_mm",    0.0)

    # Land into wind when wind is significant (>10 kts); otherwise keep heading
    safe_heading = round(wind_dir % 360) if wind_kts > 10 else heading

    # ── Action guidance ───────────────────────────────────────────────────────
    if slope > 10:
        action = (
            f"AVOID area — steep terrain ({slope}°). "
            f"Turn to {safe_heading}° and seek flat ground."
        )
    elif agl < 500:
        action = (
            f"LOW ALTITUDE — initiate landing approach immediately. "
            f"Align {safe_heading}° (into wind)."
        )
    elif agl < 2000:
        action = (
            f"Prepare for emergency landing. Identify suitable zone. "
            f"Recommended heading {safe_heading}°."
        )
    elif wind_kts > 30:
        action = (
            f"HIGH WIND: {wind_kts} kts. Turn to {safe_heading}° to land into wind. "
            f"Reduce speed to {recommended_speed} kts."
        )
    elif vis_m < 3000:
        action = (
            f"LOW VISIBILITY: {round(vis_m)} m. Maintain {safe_heading}°. "
            f"Identify LZ before descending below {round(agl * 0.5)} ft AGL."
        )
    elif precip_mm > 5:
        action = (
            f"PRECIPITATION active. Expect surface contamination. "
            f"Maintain heading {safe_heading}°, increase approach speed margin."
        )
    else:
        action = (
            f"Maintain heading {safe_heading}°. Monitor terrain ahead."
        )

    return {
        "action":                action,
        "recommended_speed_kts": round(recommended_speed, 1),
        "safe_heading_deg":      safe_heading,
        "agl_ft":                round(agl, 0),
        "time_to_ground_min":    round(time_to_ground_min, 2),
    }
