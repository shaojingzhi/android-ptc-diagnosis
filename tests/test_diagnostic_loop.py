from pathlib import Path

from android_ptc_diag.cases import load_runtime_state
from android_ptc_diag.diagnostic_loop import run_read_only_diagnosis_loop
from android_ptc_diag.models import DirectDiagnosis
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
