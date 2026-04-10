import numpy as np
import math

AHP_WEIGHTS_LAND = {
    "slope": 0.35,
    "roughness": 0.20,
    "distance": 0.15,
    "surface": 0.10,
    "crowd": 0.15,
    "obstacle": 0.05
}

AHP_WEIGHTS_WATER = {
    "distance": 0.30,
    "wind": 0.25,
    "surface": 0.20,
    "crowd": 0.15,
    "slope": 0.10
}


def normalize_matrix(matrix):
    norm = np.sqrt((matrix ** 2).sum(axis=0))
    return matrix / (norm + 1e-6)


def topsis(matrix, weights):
    matrix = np.array(matrix)

    norm = normalize_matrix(matrix)
    w = np.array(list(weights.values()))

    weighted = norm * w

    ideal_best = weighted.min(axis=0)
    ideal_worst = weighted.max(axis=0)

    d_best = np.sqrt(((weighted - ideal_best) ** 2).sum(axis=1))
    d_worst = np.sqrt(((weighted - ideal_worst) ** 2).sum(axis=1))

    return d_worst / (d_best + d_worst + 1e-6)


def logistic(x):
    return 1 / (1 + math.exp(-5 * (x - 0.5)))


def compute_cells(cells, max_range):

    land, water = [], []

    for c in cells:
        if c["surface"] == "water":
            water.append([
                c["distance"], c["wind"], 1, c["crowd"], c["slope"]
            ])
        else:
            land.append([
                c["slope"], c["roughness"], c["distance"],
                0, c["crowd"], c["obstacle"]
            ])

    land_scores = topsis(land, AHP_WEIGHTS_LAND) if land else []
    water_scores = topsis(water, AHP_WEIGHTS_WATER) if water else []

    i_l, i_w = 0, 0

    for c in cells:
        if c["surface"] == "water":
            score = water_scores[i_w]
            i_w += 1
        else:
            score = land_scores[i_l]
            i_l += 1

        prob = logistic(score)
        risk = 1 - prob

        if risk > 0.6:
            color = "#ba2627"
        elif risk > 0.3:
            color = "#ff9c00"
        else:
            color = "#2cb64f"

        c.update({
            "risk": round(risk, 2),
            "probability": round(prob, 2),
            "color": color
        })

    return cells