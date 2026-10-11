"""Small bounded read-only diagnosis loop for deterministic replay state."""

from __future__ import annotations

import json
import inspect
from typing import Any, Callable

from .models import (
    DiagnosisLoopDecision,
    DiagnosisLoopState,
    DiagnosisLoopStep,
    DirectDiagnosis,
)
from .primitives import (
    inspect_cgroup_state,
    inspect_mount_state,
    inspect_process_state,
    inspect_service_state,
)


MAX_FOLLOWUP_CHECKS = 2
_READ_ONLY_CHECKS: dict[str, Callable[..., dict[str, Any]]] = {
    "inspect_process_state": inspect_process_state,
    "inspect_cgroup_state": inspect_cgroup_state,
    "inspect_service_state": inspect_service_state,
    "inspect_mount_state": inspect_mount_state,
}

DecisionProvider = Callable[[DirectDiagnosis, dict[str, Any] | None], DiagnosisLoopDecision]


def _validate_followup_request(
    request: Any,
    diagnosis: DirectDiagnosis,
) -> str | None:
    """Return a controller rejection reason, without interpreting evidence."""
    if not isinstance(request.uncertainty_index, int):
        return "uncertainty_index must be an integer"
    if request.uncertainty_index < 0 or request.uncertainty_index >= len(diagnosis.uncertainties):
        return "uncertainty_index is out of range"
    if request.check not in _READ_ONLY_CHECKS:
        return f"unsupported read-only check: {request.check}"
    if not isinstance(request.arguments, dict):
        return "check arguments must be an object"
    try:
        inspect.signature(_READ_ONLY_CHECKS[request.check]).bind(
            {}, **request.arguments
        )
    except TypeError as exc:
        return f"invalid check arguments: {exc}"
    return None


def _bound_uncertainty_text(request: Any, diagnosis: DirectDiagnosis) -> str | None:
    if not isinstance(request.uncertainty_index, int):
        return None
    if request.uncertainty_index < 0 or request.uncertainty_index >= len(diagnosis.uncertainties):
        return None
    return diagnosis.uncertainties[request.uncertainty_index]


def run_semantic_diagnosis_loop(
    diagnosis: DirectDiagnosis,
    runtime_state: dict[str, Any],
    decision_provider: DecisionProvider,
) -> DiagnosisLoopState:
    """Let a decision provider request at most two validated read-only checks."""
    state = DiagnosisLoopState(
        current_diagnosis=diagnosis.model_copy(deep=True),
        uncertainties=list(diagnosis.uncertainties),
    )
    seen_results: set[str] = set()
    decision = decision_provider(state.current_diagnosis, None)
    state.current_diagnosis = decision.diagnosis.model_copy(deep=True)
    state.uncertainties = list(state.current_diagnosis.uncertainties)

    while decision.followup_request is not None:
        request = decision.followup_request
        if len(state.steps) >= MAX_FOLLOWUP_CHECKS:
            state.stop_reason = "maximum follow-up checks reached"
            break
        rejection = _validate_followup_request(request, state.current_diagnosis)
        if rejection is not None:
            state.stop_reason = rejection
            break

        try:
            returned_evidence = _READ_ONLY_CHECKS[request.check](
                runtime_state, **request.arguments
            )
        except Exception as exc:  # noqa: BLE001 - evidence is returned to the provider
            returned_evidence = {"error": str(exc), "read_only": True}

        result_key = json.dumps(returned_evidence, sort_keys=True, ensure_ascii=False)
        if result_key in seen_results:
            state.stop_reason = "no new information"
            break
        seen_results.add(result_key)

        prior_diagnosis = state.current_diagnosis
        bound_uncertainty_text = _bound_uncertainty_text(request, prior_diagnosis)
        next_decision = decision_provider(prior_diagnosis, returned_evidence)
        updated_diagnosis = next_decision.diagnosis.model_copy(deep=True)
        step = DiagnosisLoopStep(
            current_diagnosis=prior_diagnosis,
            uncertainties=list(prior_diagnosis.uncertainties),
            uncertainty_index=request.uncertainty_index,
            bound_uncertainty_text=bound_uncertainty_text or "",
            chosen_check=request.check,
            why_this_check=request.reason,
            check_arguments=dict(request.arguments),
            returned_evidence=returned_evidence,
            updated_diagnosis=updated_diagnosis,
            stop_reason="model chose to stop"
            if next_decision.followup_request is None
            else None,
        )
        state.steps.append(step)
        state.current_diagnosis = updated_diagnosis
        state.uncertainties = list(updated_diagnosis.uncertainties)
        decision = next_decision
        if decision.followup_request is None:
            state.stop_reason = "model chose to stop"
            break

    if state.stop_reason is None:
        state.stop_reason = "model chose to stop"
    return state


def run_read_only_diagnosis_loop(
    diagnosis: DirectDiagnosis,
    runtime_state: dict[str, Any],
    check_plan: list[dict[str, Any]],
) -> DiagnosisLoopState:
    """Run at most two checks that explicitly correspond to uncertainties."""
    state = DiagnosisLoopState(
        current_diagnosis=diagnosis.model_copy(deep=True),
        uncertainties=list(diagnosis.uncertainties),
    )
    seen_results: set[str] = set()

    for plan in check_plan:
        if len(state.steps) >= MAX_FOLLOWUP_CHECKS:
            state.stop_reason = "maximum follow-up checks reached"
            break

        chosen_check = plan.get("chosen_check")
        why_this_check = plan.get("why_this_check", "")
        arguments = plan.get("check_arguments", {})
        uncertainty = plan.get("uncertainty")
        if chosen_check not in _READ_ONLY_CHECKS:
            state.stop_reason = f"unsupported read-only check: {chosen_check}"
            break
        if uncertainty not in state.uncertainties:
            state.stop_reason = "check is not tied to a current uncertainty"
            break
        uncertainty_index = state.uncertainties.index(uncertainty)
        if not isinstance(arguments, dict):
            state.stop_reason = "check arguments must be an object"
            break

        try:
            returned_evidence = _READ_ONLY_CHECKS[chosen_check](runtime_state, **arguments)
        except Exception as exc:  # noqa: BLE001 - loop records deterministic check errors
            returned_evidence = {"error": str(exc), "read_only": True}

        result_key = json.dumps(returned_evidence, sort_keys=True, ensure_ascii=False)
        updated = state.current_diagnosis.model_copy(deep=True)
        updated.notes.append(f"read-only check {chosen_check} returned evidence")
        step_stop_reason = None
        if result_key in seen_results:
            step_stop_reason = "no new information"
        else:
            seen_results.add(result_key)

        step = DiagnosisLoopStep(
            current_diagnosis=state.current_diagnosis,
            uncertainties=list(state.uncertainties),
            uncertainty_index=uncertainty_index,
            bound_uncertainty_text=uncertainty,
            chosen_check=chosen_check,
            why_this_check=why_this_check,
            check_arguments=arguments,
            returned_evidence=returned_evidence,
            updated_diagnosis=updated,
            stop_reason=step_stop_reason,
        )
        state.steps.append(step)
        state.current_diagnosis = updated
        if step_stop_reason:
            state.stop_reason = step_stop_reason
            break

    if state.stop_reason is None:
        if len(state.steps) >= MAX_FOLLOWUP_CHECKS:
            state.stop_reason = "maximum follow-up checks reached"
        else:
            state.stop_reason = "no further checks requested"
    return state
