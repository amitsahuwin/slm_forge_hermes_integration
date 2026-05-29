"""Accept/reject/plateau logic for the autoresearch ratchet."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Decision:
    accepted: bool
    is_plateau: bool
    canary_drift: float | None
    reason: str


def evaluate_iteration(
    *,
    new_metric: float | None,
    best_metric: float | None,
    min_delta: float,
    history_no_improvement: int,
    plateau_patience: int,
    new_canary: float | None,
    new_val: float | None,
    drift_threshold: float,
) -> Decision:
    """Decide whether to accept this iteration and whether we've plateaued."""
    if new_metric is None:
        return Decision(False, False, None, "no metric reported")

    drift = (
        abs(new_canary - new_val)
        if (new_canary is not None and new_val is not None)
        else None
    )
    drift_warning = drift is not None and drift > drift_threshold

    if best_metric is None:
        # baseline iteration — always accept
        reason = "baseline accepted"
        if drift_warning:
            reason += f" (⚠ canary drift {drift:.3f} > {drift_threshold})"
        return Decision(True, False, drift, reason)

    improvement = best_metric - new_metric  # positive means new is better (lower loss)
    accepted = improvement >= min_delta

    if accepted:
        reason = f"improved by {improvement:.4f} ≥ {min_delta}"
    else:
        reason = f"no significant improvement ({improvement:+.4f} < {min_delta})"

    if drift_warning:
        reason += f" (⚠ canary drift {drift:.3f})"

    # Plateau if accepted ratchet hasn't moved for patience iters
    is_plateau = (not accepted) and (history_no_improvement + 1 >= plateau_patience)

    return Decision(accepted, is_plateau, drift, reason)
