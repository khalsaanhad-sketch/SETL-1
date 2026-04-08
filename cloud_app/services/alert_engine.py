def compute_alerts(risk: dict, prob: dict, weather: dict | None = None) -> list:
    alerts = []
    level   = risk.get("level", "LOW")
    success = prob.get("success", 1.0)

    # ── Flight-state alerts ───────────────────────────────────────────────────
    if level == "CRITICAL":
        alerts.append({
            "severity": "CRITICAL",
            "message":  "CRITICAL RISK: Immediate action required. Declare emergency.",
        })
    elif level == "HIGH":
        alerts.append({
            "severity": "HIGH",
            "message":  "HIGH RISK: Prepare for emergency landing procedures.",
        })
    elif level == "MODERATE":
        alerts.append({
            "severity": "MODERATE",
            "message":  "MODERATE RISK: Monitor situation closely.",
        })

    if success < 0.4:
        alerts.append({
            "severity": "WARNING",
            "message":  f"Low landing success probability: {round(success * 100)}%",
        })

    # ── Weather alerts ────────────────────────────────────────────────────────
    if weather:
        wind_kts  = weather.get("wind_speed_kts",   0.0)
        vis_m     = weather.get("visibility_m",  10000.0)
        precip_mm = weather.get("precipitation_mm",  0.0)
        wind_dir  = weather.get("wind_direction_deg", 0)

        if wind_kts > 50:
            alerts.append({
                "severity": "CRITICAL",
                "message":  f"WIND: {wind_kts} kts from {wind_dir}° — approach conditions unsafe.",
            })
        elif wind_kts > 30:
            alerts.append({
                "severity": "HIGH",
                "message":  f"WIND: {wind_kts} kts from {wind_dir}° — cross-wind component near limits.",
            })

        if vis_m < 800:
            alerts.append({
                "severity": "HIGH",
                "message":  f"VISIBILITY: {round(vis_m)} m — LZ cannot be identified visually.",
            })
        elif vis_m < 3000:
            alerts.append({
                "severity": "MODERATE",
                "message":  f"VISIBILITY: {round(vis_m)} m — VMC marginal, identify LZ now.",
            })

        if precip_mm > 5:
            alerts.append({
                "severity": "WARNING",
                "message":  f"PRECIPITATION: {precip_mm} mm — expect surface contamination and braking degradation.",
            })

    if not alerts:
        alerts.append({
            "severity": "INFO",
            "message":  "Normal operations. No immediate threats detected.",
        })

    return alerts
