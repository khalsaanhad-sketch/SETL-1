import math
import random


def risk_to_color(risk: float) -> str:
    if risk < 0.25:
        return "#2cb64f"
    elif risk < 0.45:
        return "#7dc840"
    elif risk < 0.60:
        return "#d8d62b"
    elif risk < 0.75:
        return "#ff9c00"
    else:
        return "#ba2627"


def compute_grid(lat: float, lon: float, base_risk: float, grid_size: int = 8, cell_deg: float = 0.004) -> list:
    cells = []
    half = grid_size // 2
    seed = int(abs(lat * 100) % 997 + abs(lon * 100) % 997)
    rng = random.Random(seed)

    for row in range(-half, half):
        for col in range(-half, half):
            clat = lat + row * cell_deg
            clon = lon + col * cell_deg

            variation = (
                math.sin(row * 0.9 + seed * 0.01) * 0.15
                + math.cos(col * 0.7 + seed * 0.01) * 0.15
                + rng.uniform(-0.08, 0.08)
            )
            local_risk = min(1.0, max(0.0, base_risk + variation))

            corners = [
                [clat, clon],
                [clat + cell_deg, clon],
                [clat + cell_deg, clon + cell_deg],
                [clat, clon + cell_deg],
            ]

            cells.append({
                "corners": corners,
                "risk": round(local_risk, 2),
                "ground_safety": round(1.0 - local_risk, 2),
                "slope_deg": round(local_risk * 18, 1),
                "obstacle": "Possible" if local_risk > 0.6 else "None",
                "color": risk_to_color(local_risk),
            })

    return cells


def compute_landing_zones(cells: list) -> list:
    sorted_cells = sorted(cells, key=lambda c: c["risk"])
    labels = ["Primary LZ", "Secondary LZ", "Emergency LZ"]
    zones = []
    for i, cell in enumerate(sorted_cells[:3]):
        zones.append({
            "corners": cell["corners"],
            "label": labels[i],
            "risk": cell["risk"],
        })
    return zones
