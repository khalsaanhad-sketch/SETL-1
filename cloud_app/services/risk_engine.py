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
            wind_kts      = weather.get("wind_speed_kts", 0.0)
            wind_gust_kts = weather.get("wind_gust_kts", wind_kts)
            effective_wind = max(wind_kts, wind_gust_kts)
            if effective_wind > 50:
                weather_risk = 0.10
            elif effective_wind > 30:
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
        wind_kts      = weather.get("wind_speed_kts",    0.0)
        wind_gust_kts = weather.get("wind_gust_kts",  wind_kts)
        vis_m         = weather.get("visibility_m",   10000.0)
        precip_mm     = weather.get("precipitation_mm",  0.0)
        ceiling_ft    = weather.get("ceiling_ft",         None)

        # Wind: use the worse of steady wind and gust for risk assessment.
        # Gusts define actual aircraft control difficulty, not mean wind.
        # >50 kts: approach essentially unsafe; >35 kts: controllability degrades.
        # Gust spread ≥15 kts adds extra risk even when mean wind is acceptable.
        effective_wind = max(wind_kts, wind_gust_kts)
        gust_spread    = wind_gust_kts - wind_kts
        if effective_wind > 50:
            weather_risk += 0.35
        elif effective_wind > 35:
            weather_risk += 0.20
        elif effective_wind > 20:
            weather_risk += 0.08
        if gust_spread >= 15:
            weather_risk += 0.08   # Significant gust spread → unstable approach

        # Ceiling: required for visual identification of any LZ.
        # <200 ft: below IFR minimums for any approach — LZ cannot be seen.
        # <500 ft: marginal; emergency visual approach extremely difficult.
        # <1000 ft: reduced situational awareness for terrain avoidance.
        if ceiling_ft is not None:
            if ceiling_ft < 200:
                weather_risk += 0.30
            elif ceiling_ft < 500:
                weather_risk += 0.20
            elif ceiling_ft < 1000:
                weather_risk += 0.10

        # Visibility: <3000 m VMC marginal; <800 m LZ not visually identifiable
        if vis_m < 800:
            weather_risk += 0.25
        elif vis_m < 3000:
            weather_risk += 0.15

        # Precipitation: graduated from light to thunderstorm.
        # TS (10 mm/h) → severe turbulence, lightning, windshear.
        # Heavy (8 mm/h) → surface contamination, braking action nil.
        # Moderate (3 mm/h) → degraded braking.
        if precip_mm >= 8:
            weather_risk += 0.20
        elif precip_mm >= 3:
            weather_risk += 0.12
        elif precip_mm > 0:
            weather_risk += 0.05

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
