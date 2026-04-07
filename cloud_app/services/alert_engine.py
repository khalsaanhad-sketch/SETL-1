def compute_alerts(risk: dict, prob: dict) -> list:
    alerts = []
    level = risk.get("level", "LOW")
    success = prob.get("success", 1.0)

    if level == "CRITICAL":
        alerts.append({
            "severity": "CRITICAL",
            "message": "CRITICAL RISK: Immediate action required. Declare emergency."
        })
    elif level == "HIGH":
        alerts.append({
            "severity": "HIGH",
            "message": "HIGH RISK: Prepare for emergency landing procedures."
        })
    elif level == "MODERATE":
        alerts.append({
            "severity": "MODERATE",
            "message": "MODERATE RISK: Monitor situation closely."
        })

    if success < 0.4:
        alerts.append({
            "severity": "WARNING",
            "message": f"Low landing success probability: {round(success * 100)}%"
        })

    if not alerts:
        alerts.append({
            "severity": "INFO",
            "message": "Normal operations. No immediate threats detected."
        })

    return alerts
