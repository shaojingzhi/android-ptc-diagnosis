"""Small bounded read-only diagnosis loop for deterministic replay state."""

from __future__ import annotations

import json
from typing import Any, Callable

from .models import DiagnosisLoopState, DiagnosisLoopStep, DirectDiagnosis
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
