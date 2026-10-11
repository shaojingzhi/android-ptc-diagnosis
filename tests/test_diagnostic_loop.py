from pathlib import Path

from android_ptc_diag.cases import load_runtime_state
from android_ptc_diag.diagnostic_loop import (
    run_read_only_diagnosis_loop,
    run_semantic_diagnosis_loop,
)
from android_ptc_diag.models import DiagnosisLoopDecision, DirectDiagnosis, FollowupRequest
from android_ptc_diag.primitives import (
    inspect_cgroup_state,
    inspect_mount_state,
    inspect_process_state,
    inspect_service_state,
)


CASE_DIR = Path(__file__).parents[1] / "eval" / "cases" / "runtime_process_killed_001"


def _diagnosis():
    return DirectDiagnosis(
        primary_candidate_indices=[1],
        evidence_indices=[27, 28],
        propagation_event_indices=[2, 10, 22, 26],
        uncertainties=[
            "why the runtime service process died is not established",
            "cross-source order unknown",
        ],
        recommended_next_checks=[
            "Inspect runtime service process and cgroup state",
            "Check runtime service readiness and required dependency state",
        ],
    )


def test_runtime_state_primitives_are_read_only_and_fixture_backed():
    state = load_runtime_state(CASE_DIR)

    process = inspect_process_state(state, name="runtime_service")
    cgroup = inspect_cgroup_state(state, name="runtime_service")
    service = inspect_service_state(state, name="runtime_service")
    mount = inspect_mount_state(state, path="/container/runtime")

    assert process["state"] == "dead"
    assert cgroup["oom_kill_count"] == 1
    assert service["state"] == "unavailable"
    assert mount["available"] is True
    assert all(result["read_only"] is True for result in (process, cgroup, service, mount))


def test_bounded_loop_records_two_read_only_checks_and_stops():
    state = load_runtime_state(CASE_DIR)
    result = run_read_only_diagnosis_loop(
        _diagnosis(),
        state,
        [
            {
                "chosen_check": "inspect_process_state",
                "why_this_check": "Check whether the named runtime process is still alive.",
                "check_arguments": {"name": "runtime_service"},
                "uncertainty": "why the runtime service process died is not established",
            },
            {
                "chosen_check": "inspect_cgroup_state",
                "why_this_check": "Check whether cgroup memory pressure is observable.",
                "check_arguments": {"name": "runtime_service"},
                "uncertainty": "cross-source order unknown",
            },
            {
                "chosen_check": "inspect_service_state",
                "why_this_check": "Check service readiness after the process check.",
                "check_arguments": {"name": "runtime_service"},
                "uncertainty": "cross-source order unknown",
            },
        ],
    )

    assert len(result.steps) == 2
    assert result.steps[0].returned_evidence["state"] == "dead"
    assert result.steps[1].returned_evidence["oom_kill_count"] == 1
    assert result.steps[0].updated_diagnosis.notes
    assert result.stop_reason == "maximum follow-up checks reached"


def test_loop_stops_when_a_check_adds_no_new_information():
    state = load_runtime_state(CASE_DIR)
    plan = {
        "chosen_check": "inspect_process_state",
        "why_this_check": "Read the runtime process state.",
        "check_arguments": {"name": "runtime_service"},
        "uncertainty": "why the runtime service process died is not established",
    }

    result = run_read_only_diagnosis_loop(_diagnosis(), state, [plan, plan])

    assert len(result.steps) == 2
    assert result.steps[1].stop_reason == "no new information"
    assert result.stop_reason == "no new information"


def test_loop_rejects_check_not_tied_to_current_uncertainty():
    state = load_runtime_state(CASE_DIR)
    result = run_read_only_diagnosis_loop(
        _diagnosis(),
        state,
        [
            {
                "chosen_check": "inspect_service_state",
                "why_this_check": "Check service readiness.",
                "check_arguments": {"name": "runtime_service"},
                "uncertainty": "an uncertainty not present in the diagnosis",
            }
        ],
    )

    assert result.steps == []
    assert result.stop_reason == "check is not tied to a current uncertainty"


def _provider_script(decisions):
    calls = []
    remaining = iter(decisions)

    def provider(diagnosis, returned_evidence):
        calls.append((diagnosis, returned_evidence))
        return next(remaining)

    provider.calls = calls
    return provider


def _request(check, uncertainty="why the runtime service process died is not established"):
    return FollowupRequest(
        uncertainty=uncertainty,
        check=check,
        arguments={"name": "runtime_service"},
        reason=f"Inspect {check} to narrow the uncertainty.",
    )


def test_semantic_loop_passes_evidence_to_provider_and_updates_diagnosis():
    updated = _diagnosis().model_copy(deep=True)
    updated.evidence_indices = [27, 28, 29]
    updated.uncertainties = ["cross-source order unknown"]
    provider = _provider_script(
        [
            DiagnosisLoopDecision(diagnosis=_diagnosis(), followup_request=_request("inspect_process_state")),
            DiagnosisLoopDecision(diagnosis=updated),
        ]
    )

    result = run_semantic_diagnosis_loop(_diagnosis(), load_runtime_state(CASE_DIR), provider)

    assert len(result.steps) == 1
    assert provider.calls[1][1]["state"] == "dead"
    assert result.current_diagnosis.evidence_indices == [27, 28, 29]
    assert result.current_diagnosis.uncertainties == ["cross-source order unknown"]
    assert result.stop_reason == "model chose to stop"
    assert "followup_request" not in result.current_diagnosis.model_dump()


def test_semantic_loop_allows_model_to_stop_before_any_check():
    diagnosis = _diagnosis().model_copy(deep=True)
    diagnosis.notes.append("Evidence is sufficient for this pass.")
    provider = _provider_script([DiagnosisLoopDecision(diagnosis=diagnosis)])

    result = run_semantic_diagnosis_loop(_diagnosis(), load_runtime_state(CASE_DIR), provider)

    assert result.steps == []
    assert result.stop_reason == "model chose to stop"
    assert result.current_diagnosis.notes == ["Evidence is sufficient for this pass."]


def test_semantic_loop_stops_at_followup_budget():
    provider = _provider_script(
        [
            DiagnosisLoopDecision(diagnosis=_diagnosis(), followup_request=_request("inspect_process_state")),
            DiagnosisLoopDecision(
                diagnosis=_diagnosis(),
                followup_request=_request(
                    "inspect_cgroup_state", "cross-source order unknown"
                ),
            ),
            DiagnosisLoopDecision(
                diagnosis=_diagnosis(),
                followup_request=_request("inspect_service_state", "cross-source order unknown"),
            ),
        ]
    )

    result = run_semantic_diagnosis_loop(_diagnosis(), load_runtime_state(CASE_DIR), provider)

    assert len(result.steps) == 2
    assert result.stop_reason == "maximum follow-up checks reached"
    assert len(provider.calls) == 3


def test_semantic_loop_rejects_invalid_uncertainty_and_write_like_check():
    invalid_uncertainty = _provider_script(
        [
            DiagnosisLoopDecision(
                diagnosis=_diagnosis(),
                followup_request=_request("inspect_process_state", "not a current uncertainty"),
            )
        ]
    )
    result = run_semantic_diagnosis_loop(
        _diagnosis(), load_runtime_state(CASE_DIR), invalid_uncertainty
    )
    assert result.steps == []
    assert result.stop_reason == "check is not tied to a current uncertainty"

    write_like = _provider_script(
        [
            DiagnosisLoopDecision(
                diagnosis=_diagnosis(),
                followup_request=FollowupRequest(
                    uncertainty="why the runtime service process died is not established",
                    check="restart_service",
                    arguments={"name": "runtime_service"},
                    reason="Try to restore service availability.",
                ),
            )
        ]
    )
    result = run_semantic_diagnosis_loop(_diagnosis(), load_runtime_state(CASE_DIR), write_like)
    assert result.steps == []
    assert result.stop_reason == "unsupported read-only check: restart_service"

    invalid_arguments = _provider_script(
        [
            DiagnosisLoopDecision(
                diagnosis=_diagnosis(),
                followup_request=FollowupRequest(
                    uncertainty="why the runtime service process died is not established",
                    check="inspect_process_state",
                    arguments={"name": "runtime_service", "write": True},
                    reason="Use an unsupported argument.",
                ),
            )
        ]
    )
    result = run_semantic_diagnosis_loop(
        _diagnosis(), load_runtime_state(CASE_DIR), invalid_arguments
    )
    assert result.steps == []
    assert result.stop_reason.startswith("invalid check arguments:")


def test_semantic_loop_stops_on_duplicate_evidence_without_second_provider_update():
    provider = _provider_script(
        [
            DiagnosisLoopDecision(diagnosis=_diagnosis(), followup_request=_request("inspect_process_state")),
            DiagnosisLoopDecision(
                diagnosis=_diagnosis(),
                followup_request=_request("inspect_process_state"),
            ),
        ]
    )

    result = run_semantic_diagnosis_loop(_diagnosis(), load_runtime_state(CASE_DIR), provider)

    assert len(result.steps) == 1
    assert result.stop_reason == "no new information"
    assert len(provider.calls) == 2
