def compute_risk(state: dict, weather: dict | None = None) -> dict:
    altitude = state.get("altitude_ft", 5000)
    speed    = state.get("speed_kts", 100)
    heading  = state.get("heading_deg", 90)

    # ── Landing / ground state ────────────────────────────────────────────────
    # An aircraft at very low altitude and low speed is on the ground or
    # rolling out — risk is near zero regardless of other factors.
    # Thresholds: ≤100 ft AGL and ≤60 kts ground speed.
    if altitude <= 100 and speed <= 60:
        weather_risk = 0.0
        if weather:
            wind_kts = weather.get("wind_speed_kts", 0.0)
            if wind_kts > 50:
                weather_risk = 0.10
            elif wind_kts > 30:
                weather_risk = 0.05
        overall = round(min(0.15, weather_risk), 3)
        return {
            "overall":       overall,
            "level":         "LOW",
            "altitude_risk": 0.0,
            "speed_risk":    round(speed / 300.0, 3),
            "weather_risk":  round(weather_risk, 3),
            "heading_deg":   heading,
            "flight_state":  "landed",
        }

    # ── Normal altitude/speed risk ────────────────────────────────────────────
    # altitude_risk: urgency increases as altitude decreases (less time to act).
    # Clamped so that 100 ft (just above landing threshold) = 0.99 not 1.0.
    altitude_risk = max(0.0, 1.0 - (max(altitude, 101) / 10000.0))
    speed_risk    = min(1.0, speed / 300.0)

    base = round(altitude_risk * 0.6 + speed_risk * 0.4, 3)

    # ── Weather modifiers ─────────────────────────────────────────────────────
    weather_risk = 0.0
    if weather:
        wind_kts    = weather.get("wind_speed_kts",    0.0)
        vis_m       = weather.get("visibility_m",   10000.0)
        precip_mm   = weather.get("precipitation_mm",  0.0)

        # Wind: >30 kts controllability degrades; >50 kts approach essentially unsafe
        if wind_kts > 50:
            weather_risk += 0.35
        elif wind_kts > 30:
            weather_risk += 0.15

        # Visibility: <3000 m VMC marginal; <800 m LZ not visually identifiable
        if vis_m < 800:
            weather_risk += 0.25
        elif vis_m < 3000:
            weather_risk += 0.15

        # Precipitation: >5 mm/h contaminated surface, reduced braking
        if precip_mm > 5:
            weather_risk += 0.10

    overall = round(min(1.0, base + weather_risk), 3)

    level = "LOW"
    if overall > 0.7:
        level = "CRITICAL"
    elif overall > 0.5:
        level = "HIGH"
    elif overall > 0.3:
        level = "MODERATE"

    return {
        "overall":        overall,
        "level":          level,
        "altitude_risk":  round(altitude_risk, 3),
        "speed_risk":     round(speed_risk, 3),
        "weather_risk":   round(weather_risk, 3),
        "heading_deg":    heading,
    }
