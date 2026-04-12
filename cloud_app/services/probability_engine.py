import math


def compute_probability(risk: dict) -> dict:
    """
    Converts the scalar risk score into calibrated probability outputs using
    a logistic function instead of a linear inversion.

    Logistic (k=8) gives a proper S-curve:
        overall=0.1  → success ~96%  (clearly safe)
        overall=0.3  → success ~83%
        overall=0.5  → success  50%  (decision boundary)
        overall=0.7  → success ~17%
        overall=0.9  → success  ~4%  (clearly critical)

    Uncertainty is epistemic — maximum at the decision boundary where the
    model is least sure, minimum when risk is unambiguously low or high.
    Confidence is the complement of uncertainty.
    """
    overall = float(risk.get("overall", 0.5))
    overall = max(0.0, min(1.0, overall))

    # k=8.0 for overall aircraft risk (sharper S-curve than per-cell k=5.0 in decision_engine).
    # Intentional: overall risk needs a tighter decision boundary for go/no-go.
    k            = 8.0
    success_prob = round(1.0 / (1.0 + math.exp(k * (overall - 0.5))), 3)
    failure_prob = round(1.0 - success_prob, 3)

    # Epistemic uncertainty: 1.0 at 50/50, 0.0 when fully certain either way
    uncertain_prob = round(max(0.0, 1.0 - abs(2.0 * success_prob - 1.0)), 3)
    confidence     = round(1.0 - uncertain_prob, 3)

    return {
        "success":    success_prob,
        "failure":    failure_prob,
        "uncertain":  uncertain_prob,
        "confidence": confidence,
    }
