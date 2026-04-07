def compute_probability(risk: dict) -> dict:
    overall = risk.get("overall", 0.5)

    success_prob = round(max(0.0, 1.0 - overall), 3)
    failure_prob = round(overall, 3)
    uncertain_prob = round(1.0 - success_prob - failure_prob, 3)

    return {
        "success": success_prob,
        "failure": failure_prob,
        "uncertain": max(0.0, uncertain_prob),
        "confidence": round(1.0 - abs(uncertain_prob), 3)
    }
