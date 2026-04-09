"""
SETL Decision Engine — AHP → TOPSIS → Logistic pipeline.

Replaces the linear risk formula in generate_cells() with a multi-criteria
decision-making (MCDM) approach used in real emergency-landing research.

Pipeline:
  1. AHP weights  — domain-justified importance per criterion (land vs water)
  2. TOPSIS       — ranks cells relative to ideal-best / ideal-worst
  3. Logistic     — maps the TOPSIS score to a non-linear probability in [0,1]
  4. Absolute floor — prevents genuinely safe terrain being marked dangerous
     purely due to relative comparison against equally-safe neighbours
  5. risk = 1 - probability

No changes to session management, WebSocket IDs, or any other engine.
"""

import math

import numpy as np

# ── AHP weights ────────────────────────────────────────────────────────────────
# Columns must match the order of features built in score_cells() below.
# surface_score removed — it is derived from slope and would double-count it.

# Land: [slope, roughness, distance, crowd, obstacle]
_LAND_KEYS = ["slope", "roughness", "distance", "crowd", "obstacle"]
_LAND_W    = [  0.38,      0.22,       0.18,     0.14,    0.08  ]

# Water: [distance, wind, crowd, slope]
_WATER_KEYS = ["distance", "wind", "crowd", "slope"]
_WATER_W    = [   0.35,     0.30,   0.20,    0.15  ]


# ── TOPSIS ─────────────────────────────────────────────────────────────────────

def _topsis(matrix: np.ndarray, weights: list) -> np.ndarray:
    """
    Return TOPSIS closeness scores (higher = safer site) for each row.

    Steps:
      1. Column-wise Euclidean normalisation
      2. Multiply by AHP weights
      3. Identify ideal-best (column min) and ideal-worst (column max)
      4. Closeness = d_worst / (d_best + d_worst)
    """
    denom = np.linalg.norm(matrix, axis=0)
    denom[denom == 0] = 1e-9
    n = matrix / denom
    v = n * np.array(weights, dtype=float)

    ideal_best  = v.min(axis=0)
    ideal_worst = v.max(axis=0)

    d_best  = np.sqrt(((v - ideal_best)  ** 2).sum(axis=1))
    d_worst = np.sqrt(((v - ideal_worst) ** 2).sum(axis=1))

    return d_worst / (d_best + d_worst + 1e-9)


# ── Logistic ───────────────────────────────────────────────────────────────────

def _logistic(x: float, k: float = 5.0) -> float:
    """Sigmoid centred at 0.5 with steepness k.  Maps [0,1]→[0,1] non-linearly."""
    return 1.0 / (1.0 + math.exp(-k * (x - 0.5)))


# ── Absolute safety floor ──────────────────────────────────────────────────────

def _apply_land_floor(prob: float, slope: float, roughness: float,
                      crowd: float, obstacle: float) -> float:
    """
    Prevent the purely relative TOPSIS comparison from marking genuinely safe
    flat terrain as high-risk just because the neighbouring cells happen to be
    similarly safe.

    Thresholds are intentionally conservative:
      • slope     : degrees
      • roughness : metres (DEM std-dev in a 3×3 neighbourhood via etopo1)
      • crowd/obs : normalised [0, 1]
    """
    # Very flat, clear terrain (runway / airstrip quality) → green floor
    if slope < 2.0 and roughness < 3.0 and crowd < 0.15 and obstacle < 0.15:
        return max(prob, 0.76)

    # Flat, open terrain (good emergency LZ) → at worst amber
    if slope < 5.0 and roughness < 8.0 and crowd < 0.35 and obstacle < 0.30:
        return max(prob, 0.55)

    return prob


# ── Colour palette ─────────────────────────────────────────────────────────────

def _risk_color(risk: float, is_water: bool) -> str:
    if is_water:
        if   risk > 0.85: return "#1e3a5f"   # deep / very high risk — navy
        elif risk > 0.75: return "#1d4ed8"   # mid ocean — blue
        elif risk > 0.65: return "#0284c7"   # continental shelf — sky blue
        else:             return "#06b6d4"   # shallow coastal — cyan
    else:
        if   risk > 0.60: return "#ba2627"   # red   — critical
        elif risk > 0.45: return "#ff9c00"   # amber — high
        elif risk > 0.25: return "#d8d62b"   # yellow — moderate
        else:             return "#2cb64f"   # green  — low


# ── Public API ─────────────────────────────────────────────────────────────────

def score_cells(cells: list) -> list:
    """
    Apply AHP → TOPSIS → Logistic → absolute floor to a list of cell dicts
    and return them with 'risk', 'probability', and 'color' fields added /
    overwritten.

    Expected fields per cell (missing ones default to 0):
        is_water  bool
        slope     float   degrees
        roughness float   elevation std-dev in 3×3 neighbourhood (metres)
        distance  float   km from aircraft
        wind      float   m/s (from weather)
        crowd     float   [0,1]  (0 when no data — conservative default)
        obstacle  float   [0,1]  (0 when no data)
    """
    if not cells:
        return cells

    land_idx,  water_idx  = [], []
    land_rows, water_rows = [], []

    for idx, c in enumerate(cells):
        slope  = float(c.get("slope",     0.0))
        rough  = float(c.get("roughness", 0.0))
        dist   = float(c.get("distance",  0.0))
        crowd  = float(c.get("crowd",     0.0))
        obst   = float(c.get("obstacle",  0.0))
        wind   = float(c.get("wind",      0.0))

        if c.get("is_water"):
            water_idx.append(idx)
            water_rows.append([dist, wind, crowd, slope])
        else:
            land_idx.append(idx)
            land_rows.append([slope, rough, dist, crowd, obst])

    if land_rows:
        scores = _topsis(np.array(land_rows, dtype=float), _LAND_W)
        for rank, idx in enumerate(land_idx):
            prob = _logistic(float(scores[rank]))

            # Apply absolute floor so safe terrain isn't painted red by
            # relative comparison with equally-safe neighbouring cells
            s, r, c, o = (land_rows[rank][0], land_rows[rank][1],
                          land_rows[rank][3], land_rows[rank][4])
            prob = _apply_land_floor(prob, s, r, c, o)

            risk = round(1.0 - prob, 3)
            cells[idx]["risk"]        = risk
            cells[idx]["probability"] = round(prob, 3)
            cells[idx]["color"]       = _risk_color(risk, is_water=False)

    if water_rows:
        scores = _topsis(np.array(water_rows, dtype=float), _WATER_W)
        for rank, idx in enumerate(water_idx):
            prob = _logistic(float(scores[rank]))
            risk = round(1.0 - prob, 3)
            cells[idx]["risk"]        = risk
            cells[idx]["probability"] = round(prob, 3)
            cells[idx]["color"]       = _risk_color(risk, is_water=True)

    return cells
