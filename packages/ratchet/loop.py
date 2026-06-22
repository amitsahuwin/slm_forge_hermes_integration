"""The autoresearch ratchet loop. Runs one session to completion."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from packages.ratchet.decision import evaluate_iteration
from packages.ratchet.hermes_bridge import (
    MutationProposal,
    MutationProposalError,
    propose_mutation,
)

log = logging.getLogger("ratchet.loop")


class API:
    """Tiny HTTP wrapper around the SLM-Forge API."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.c = httpx.Client(timeout=30)

    def get_session(self, sid: int) -> dict:
        return self.c.get(f"{self.base}/api/v1/sessions/{sid}").raise_for_status().json()

    def patch_session(self, sid: int, **fields: Any) -> None:
        self.c.patch(f"{self.base}/api/v1/sessions/{sid}", json=fields).raise_for_status()

    def list_iterations(self, sid: int) -> list[dict]:
        r = self.c.get(f"{self.base}/api/v1/sessions/{sid}/iterations")
        r.raise_for_status()
        return r.json()

    def create_run(self, payload: dict) -> dict:
        r = self.c.post(f"{self.base}/api/v1/runs", json=payload)
        r.raise_for_status()
        return r.json()

    def get_run(self, rid: int) -> dict:
        r = self.c.get(f"{self.base}/api/v1/runs/{rid}")
        r.raise_for_status()
        return r.json()

    def patch_run(self, rid: int, **fields: Any) -> None:
        self.c.patch(f"{self.base}/api/v1/runs/{rid}", json=fields).raise_for_status()


def _hyperparams_from_session(session: dict) -> dict:
    """Initial (baseline) hyperparams come from the session row."""
    return {
        "iters": session["iters"],
        "batch_size": session["batch_size"],
        "learning_rate": session["learning_rate"],
        "num_layers": session["num_layers"],
        "max_seq_length": session["max_seq_length"],
    }


def _apply_mutation(base: dict, m: MutationProposal) -> dict:
    """Return a new hyperparam dict with the mutation applied."""
    out = dict(base)
    if m.learning_rate is not None:
        out["learning_rate"] = m.learning_rate
    if m.batch_size is not None:
        out["batch_size"] = m.batch_size
    if m.num_layers is not None:
        out["num_layers"] = m.num_layers
    if m.iters is not None:
        out["iters"] = m.iters
    if m.max_seq_length is not None:
        out["max_seq_length"] = m.max_seq_length
    return out


def _wait_for_run(api: API, rid: int, *, poll: float = 2.0, timeout: float = 7200) -> dict:
    """Block until a run reaches terminal status."""
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        r = api.get_run(rid)
        if r["status"] != last_status:
            log.info("  run #%s status: %s", rid, r["status"])
            last_status = r["status"]
        if r["status"] in {"completed", "failed", "cancelled"}:
            return r
        time.sleep(poll)
    raise TimeoutError(f"Run #{rid} did not finish in {timeout}s")


def _history_summary(iterations: list[dict]) -> list[dict]:
    """Compact iteration history for the LLM prompt."""
    return [
        {
            "iter": it.get("iteration_number"),
            "lr": it["learning_rate"],
            "batch_size": it["batch_size"],
            "num_layers": it["num_layers"],
            "iters": it["iters"],
            "val_loss": it.get("final_val_loss"),
            "canary_loss": it.get("canary_loss"),
            "was_accepted": it.get("was_accepted"),
        }
        for it in iterations
    ]


def run_session(session_id: int, api: API) -> None:
    """Orchestrate one autoresearch session to completion."""
    session = api.get_session(session_id)
    log.info("─── Session #%s: %s ───", session_id, session["name"])
    log.info("  dataset=%s model=%s method=%s backend=%s",
             session["dataset"], session["base_model"], session["method"],
             session.get("trainer_backend", "mlx"))
    log.info("  max_rounds=%s plateau_patience=%s min_delta=%s",
             session["max_rounds"], session["plateau_patience"], session["min_delta"])

    api.patch_session(session_id, status="running")

    base_hyperparams = _hyperparams_from_session(session)
    best_metric: float | None = None
    best_run_id: int | None = None
    no_improvement_streak = 0
    # PR-1 A2 — track consecutive unparseable proposals; abort the session
    # after ``HERMES_MAX_PROPOSAL_FAILURES`` rather than silently fabricating
    # a do-nothing mutation as the old code did.
    proposal_failure_streak = 0
    import os as _os  # local import — avoids module-load surprises in tests
    max_proposal_failures = int(_os.environ.get("HERMES_MAX_PROPOSAL_FAILURES", "3"))

    for round_idx in range(session["max_rounds"]):
        api.patch_session(session_id, current_round=round_idx)

        # ─── decide hyperparams for this iteration ───
        if round_idx == 0:
            hp = base_hyperparams
            mutation_reasoning = "baseline"
        else:
            iters_so_far = api.list_iterations(session_id)
            hist = _history_summary(iters_so_far)
            log.info("  asking Hermes for mutation (history=%d iters)", len(hist))
            try:
                proposal = propose_mutation(
                    dataset=session["dataset"],
                    history=hist,
                    current_best_metric=best_metric,
                )
            except MutationProposalError as e:
                # PR-1 A2 — surface the failure instead of fabricating a fake proposal.
                proposal_failure_streak += 1
                log.warning(
                    "Hermes proposal unparseable (streak=%d/%d): %s",
                    proposal_failure_streak,
                    max_proposal_failures,
                    e,
                )
                if proposal_failure_streak >= max_proposal_failures:
                    log.error(
                        "Aborting session — %d consecutive proposal failures",
                        proposal_failure_streak,
                    )
                    api.patch_session(session_id, status="failed")
                    return
                # Skip this iteration cleanly — no run created, no mutation faked.
                continue
            else:
                proposal_failure_streak = 0
            # Apply mutation on top of the BEST-so-far config (or baseline if no best yet)
            mutate_from = base_hyperparams
            if best_run_id is not None:
                best = api.get_run(best_run_id)
                mutate_from = {k: best[k] for k in base_hyperparams}
            hp = _apply_mutation(mutate_from, proposal)
            mutation_reasoning = proposal.reasoning
            log.info("  mutation: %s", proposal.model_dump(exclude_none=True))

        # ─── create the Run; the trainer worker will pick it up ───
        run_payload = {
            "dataset": session["dataset"],
            "base_model": session["base_model"],
            "method": session["method"],
            # Phase U — pin every iteration to the session's backend, else runs
            # default to "mlx" and a CUDA-only host never claims them.
            "trainer_backend": session.get("trainer_backend", "mlx"),
            **hp,
            "grad_checkpoint": False,
            "seed": 0,
        }
        created = api.create_run(run_payload)
        rid = created["id"]
        log.info("  → created run #%s (round %d)", rid, round_idx)

        # Annotate it with session linkage
        api.patch_run(
            rid,
            # PATCH only accepts the fields its schema lists; for session linkage
            # we update via a direct DB write below would be cleaner — but for
            # Phase 2 simplicity we extend the RunPatch later if needed.
        )
        # The /runs PATCH only accepts the operational fields. To set
        # session_id / iteration_number / parent_run_id / mutation_reasoning,
        # we use a small bookkeeping POST below via /sessions/{sid}/link-run.
        # For Phase 2 we shortcut by writing those fields via a dedicated endpoint
        # — but to keep router count down, we instead just patch /runs with a
        # superset payload by extending RunPatch. (Already done in routers/runs.py
        # if you've patched it; if not, this is a no-op.)
        try:
            httpx.patch(
                f"{api.base}/api/v1/runs/{rid}",
                json={
                    "session_id": session_id,
                    "iteration_number": round_idx,
                    "parent_run_id": best_run_id,
                    "mutation_reasoning": mutation_reasoning,
                },
                timeout=10,
            ).raise_for_status()
        except httpx.HTTPError as e:
            log.warning("  could not link run to session (%s) — continuing", e)

        # ─── wait for the trainer to execute it ───
        log.info("  waiting for trainer to pick up run #%s...", rid)
        final = _wait_for_run(api, rid)

        if final["status"] != "completed":
            log.warning("  run #%s ended with status=%s — marking rejected", rid, final["status"])
            try:
                httpx.patch(
                    f"{api.base}/api/v1/runs/{rid}",
                    json={"was_accepted": False},
                    timeout=10,
                )
            except httpx.HTTPError:
                pass
            no_improvement_streak += 1
            if no_improvement_streak >= session["plateau_patience"]:
                log.info("  plateau (errors) — ending session")
                break
            continue

        # ─── evaluate ───
        new_val = final.get("final_val_loss")
        new_canary = final.get("canary_loss")  # currently None until canary eval added
        decision = evaluate_iteration(
            new_metric=new_val,
            best_metric=best_metric,
            min_delta=session["min_delta"],
            history_no_improvement=no_improvement_streak,
            plateau_patience=session["plateau_patience"],
            new_canary=new_canary,
            new_val=new_val,
            drift_threshold=session["canary_drift_threshold"],
        )

        log.info("  decision: %s%s",
                 "ACCEPT" if decision.accepted else "REJECT", f" — {decision.reason}")

        try:
            httpx.patch(
                f"{api.base}/api/v1/runs/{rid}",
                json={"was_accepted": decision.accepted},
                timeout=10,
            ).raise_for_status()
        except httpx.HTTPError as e:
            log.warning("could not mark run was_accepted: %s", e)

        if decision.accepted and new_val is not None:
            best_metric = new_val
            best_run_id = rid
            no_improvement_streak = 0
            api.patch_session(
                session_id,
                best_run_id=best_run_id,
                best_metric_value=best_metric,
            )
        else:
            no_improvement_streak += 1

        if decision.is_plateau:
            log.info("  plateau detected — ending session early")
            break

    # ─── session complete ───
    api.patch_session(session_id, status="completed")

    # Auto-queue export for the session's winner (if any)
    if best_run_id is not None:
        try:
            httpx.post(
                f"{api.base}/api/v1/exports",
                json={"run_id": best_run_id, "quant_levels": ["Q4_K_M", "Q8_0"]},
                timeout=10,
            ).raise_for_status()
            log.info("  auto-queue export for best run #%s", best_run_id)
        except Exception as e:
            log.warning("  failed to auto-queue export: %s", e)
    log.info("─── Session #%s complete. Best run: #%s (val_loss=%s) ───",
             session_id, best_run_id, best_metric)
