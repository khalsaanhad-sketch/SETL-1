"""
SETL Decision Engine — AHP → TOPSIS → Logistic pipeline.

LAND AHP WEIGHTS — Scientific validation sources:
  slope     (0.35): Penn State HLZ AHP expert study; STANAG 2999 (NATO);
                    ArcGIS HLZ Solution (ESRI); ERDC GOAT v1.0 (US Army 2021);
                    MDPI Remote Sensing helicopter landing site study 2021.
  roughness (0.22): US Army ERDC GOAT (2021) — "surface roughness is a KEY DISCRIMINATOR
                    for site utility in complex terrain"; Jin et al. Transactions in GIS 2025.
  distance  (0.15): Reachability handled by glide mask; secondary within reachable set.
                    ENAC automated emergency landing selector (DASC 2022).
  crowd     (0.20): NASA forced landing specs: "risk to civilian population" = #1 criterion;
                    Di Donato & Atkins, AIAA Journal of Aerospace Information Systems;
                    MDPI Drones 2025 contingency landing; arxiv 2026 airspace-aware landing.
  obstacle  (0.08): ArcGIS HLZ (ESRI) — 3rd tier factor; Penn State HLZ study.

WATER WEIGHTS — Scientific validation sources:
  wind       (0.40): FAA AIM Section 6-3-3 — "sea conditions and wind = #1 ditching factor"
  depth_risk (0.30): AOPA — "hypothermia claims ~50% of ditching victims"; shallow coastal
                     water dramatically improves rescue time and survival probability.
                     FAA AIM: "aim for shallow water" — primary spatial guidance for ditching.
  distance   (0.20): Secondary; depth_risk carries the proximity-to-rescue signal.
  crowd      (0.10): Maritime rescue proximity proxy. BENEFIT column — coastal crowd indicates
                     rescue infrastructure proximity (opposite sign vs. land model).

Pipeline: AHP weights → TOPSIS → Logistic (k=5) → absolute floor → risk = 1 − probability
"""

import math

import numpy as np

# ── AHP weights ────────────────────────────────────────────────────────────────
# Columns must match the order of features built in score_cells() below.
# surface_score removed — it is derived from slope and would double-count it.

_LAND_KEYS = ["slope", "roughness", "distance", "crowd", "obstacle"]
_LAND_W    = [  0.35,      0.22,       0.15,     0.20,    0.08  ]

_WATER_KEYS = ["distance", "wind", "crowd", "depth_risk"]
_WATER_W    = [   0.20,     0.40,   0.10,     0.30     ]

assert abs(sum(_LAND_W)  - 1.0) < 1e-9, f"LAND weights sum to {sum(_LAND_W)}, not 1.0"
assert abs(sum(_WATER_W) - 1.0) < 1e-9, f"WATER weights sum to {sum(_WATER_W)}, not 1.0"


# ── TOPSIS ─────────────────────────────────────────────────────────────────────

def _topsis(matrix: np.ndarray, weights: list,
            cost_cols: list | None = None) -> np.ndarray:
    if cost_cols is None:
        cost_cols = list(range(matrix.shape[1]))

    denom = np.linalg.norm(matrix, axis=0)
    denom[denom == 0] = 1e-9
    n = matrix / denom
    v = n * np.array(weights, dtype=float)

    ideal_best  = np.where(
        [i in cost_cols for i in range(v.shape[1])],
        v.min(axis=0), v.max(axis=0)
    )
    ideal_worst = np.where(
        [i in cost_cols for i in range(v.shape[1])],
        v.max(axis=0), v.min(axis=0)
    )

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
            depth_risk = float(c.get("depth_risk", 0.6))
            water_rows.append([dist, wind, crowd, depth_risk])
        else:
            land_idx.append(idx)
            _d_opt  = 1.5
            _d_sig  = 3.0
            dist_cost = 1.0 - math.exp(-0.5 * ((dist - _d_opt) / _d_sig) ** 2)
            land_rows.append([slope, rough, dist_cost, crowd, obst])

    if land_rows:
        scores = _topsis(np.array(land_rows, dtype=float), _LAND_W,
                         cost_cols=[0, 1, 2, 3, 4])
        for rank, idx in enumerate(land_idx):
            prob = _logistic(float(scores[rank]))

            # Apply absolute floor so safe terrain isn't painted red by
            # relative comparison with equally-safe neighbouring cells
            s, r, c, o = (land_rows[rank][0], land_rows[rank][1],
                          land_rows[rank][3], land_rows[rank][4])
            prob = _apply_land_floor(prob, s, r, c, o)

            risk = round(1.0 - prob, 3)

            # ── Terrain clearance hard floor ───────────────────────────────
            # Altitude above this cell's terrain, regardless of TOPSIS score.
            # A ridge nearly at aircraft altitude is always critical.
            clearance_ft = float(cells[idx].get("clearance_ft", 9999))
            if clearance_ft < 200:
                risk = max(risk, 0.92)
            elif clearance_ft < 500:
                risk = max(risk, 0.72)
            elif clearance_ft < 1000:
                risk = max(risk, 0.46)

            cells[idx]["risk"]        = risk
            cells[idx]["probability"] = round(1.0 - risk, 3)
            cells[idx]["color"]       = _risk_color(risk, is_water=False)

    if water_rows:
        scores = _topsis(np.array(water_rows, dtype=float), _WATER_W,
                         cost_cols=[0, 1, 3])
        for rank, idx in enumerate(water_idx):
            prob = _logistic(float(scores[rank]))
            risk = round(1.0 - prob, 3)
            cells[idx]["risk"]        = risk
            cells[idx]["probability"] = round(prob, 3)
            cells[idx]["color"]       = _risk_color(risk, is_water=True)

    return cells
