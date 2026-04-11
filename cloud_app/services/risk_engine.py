def compute_risk(state: dict, weather: dict | None = None) -> dict:
    altitude    = state.get("altitude_ft", 5000)
    speed       = state.get("speed_kts", 100)
    heading     = state.get("heading_deg", 90)
    vs_fpm      = float(state.get("vs_fpm", 0) or 0)

    qnh_hpa      = float((weather or {}).get("qnh_hpa", 1013.25) or 1013.25)
    pressure_corr_ft = (qnh_hpa - 1013.25) * 30.0
    true_altitude = altitude + pressure_corr_ft

    if altitude <= 100 and speed <= 60:
        weather_risk = 0.0
        if weather:
            wind_kts      = weather.get("wind_speed_kts", 0.0)
            wind_gust_kts = weather.get("wind_gust_kts", wind_kts)
            effective_wind = max(wind_kts, wind_gust_kts)
            if effective_wind > 50:   weather_risk = 0.10
            elif effective_wind > 30: weather_risk = 0.05
        overall = round(min(0.15, weather_risk), 3)
        return {
            "overall":       overall,
            "level":         "LOW",
            "altitude_risk": 0.0,
            "speed_risk":    round(speed / 300.0, 3),
            "weather_risk":  round(weather_risk, 3),
            "vs_risk":       0.0,
            "heading_deg":   heading,
            "flight_state":  "landed",
            "true_altitude_ft": round(true_altitude),
        }

    altitude_risk = max(0.0, 1.0 - (max(true_altitude, 101) / 10000.0))
    speed_risk    = min(1.0, speed / 300.0)

    base = round(altitude_risk * 0.6 + speed_risk * 0.4, 3)

    vs_risk = 0.0
    if vs_fpm < -3000:   vs_risk = 0.30
    elif vs_fpm < -2000: vs_risk = 0.20
    elif vs_fpm < -1500: vs_risk = 0.12
    elif vs_fpm < -1000: vs_risk = 0.06
    elif vs_fpm < -500 and true_altitude < 3000:
        vs_risk = 0.04

    ttg_scalar = 1.0
    if vs_fpm < -50:
        ttg_s = (true_altitude / abs(vs_fpm)) * 60.0
        if ttg_s < 60:    ttg_scalar = 1.40
        elif ttg_s < 120: ttg_scalar = 1.20
        elif ttg_s < 180: ttg_scalar = 1.10

    base = round(min(1.0, base * ttg_scalar), 3)

    weather_risk = 0.0
    if weather:
        wind_kts      = weather.get("wind_speed_kts",    0.0)
        wind_gust_kts = weather.get("wind_gust_kts",  wind_kts)
        vis_m         = weather.get("visibility_m",   10000.0)
        precip_mm     = weather.get("precipitation_mm",  0.0)
        ceiling_ft    = weather.get("ceiling_ft",         None)

        effective_wind = max(wind_kts, wind_gust_kts)
        gust_spread    = wind_gust_kts - wind_kts
        if effective_wind > 50:   weather_risk += 0.35
        elif effective_wind > 35: weather_risk += 0.20
        elif effective_wind > 20: weather_risk += 0.08
        if gust_spread >= 15:     weather_risk += 0.08

        if ceiling_ft is not None:
            if ceiling_ft < 200:   weather_risk += 0.30
            elif ceiling_ft < 500: weather_risk += 0.20
            elif ceiling_ft < 1000:weather_risk += 0.10

        if vis_m < 800:    weather_risk += 0.25
        elif vis_m < 3000: weather_risk += 0.15

        if precip_mm >= 8:    weather_risk += 0.20
        elif precip_mm >= 3:  weather_risk += 0.12
        elif precip_mm > 0:   weather_risk += 0.05

    overall = round(min(1.0, base + weather_risk + vs_risk), 3)

    level = "LOW"
    if overall > 0.7:   level = "CRITICAL"
    elif overall > 0.5: level = "HIGH"
    elif overall > 0.3: level = "MODERATE"

    if vs_fpm < -2000 and true_altitude < 5000:
        flight_state = "emergency_descent"
    elif vs_fpm < -500:
        flight_state = "descending"
    elif vs_fpm > 500:
        flight_state = "climbing"
    else:
        flight_state = "cruise"

    return {
        "overall":          overall,
        "level":            level,
        "altitude_risk":    round(altitude_risk, 3),
        "speed_risk":       round(speed_risk, 3),
        "weather_risk":     round(weather_risk, 3),
        "vs_risk":          round(vs_risk, 3),
        "ttg_scalar":       round(ttg_scalar, 2),
        "heading_deg":      heading,
        "flight_state":     flight_state,
        "true_altitude_ft": round(true_altitude),
    }
