def compute_options(prob: dict) -> list:
    success = prob.get("success", 0.5)

    options = [
        {
            "type": "PRIMARY",
            "description": "Straight-ahead emergency landing",
            "success_probability": round(success * 0.95, 3),
            "recommended": success > 0.5
        },
        {
            "type": "SECONDARY",
            "description": "Turn 30° right and descend",
            "success_probability": round(success * 0.80, 3),
            "recommended": 0.3 < success <= 0.5
        },
        {
            "type": "EMERGENCY",
            "description": "Immediate forced landing",
            "success_probability": round(success * 0.60, 3),
            "recommended": success <= 0.3
        }
    ]

    return options
