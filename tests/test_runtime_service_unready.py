from pathlib import Path

from android_ptc_diag.backend import ReplayBackend
from android_ptc_diag.cases import load_case, load_runtime_state
from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.compact import build_compact_evidence
from android_ptc_diag.diagnostic_loop import run_semantic_diagnosis_loop
from android_ptc_diag.models import DiagnosisLoopDecision, DirectDiagnosis, FollowupRequest
from android_ptc_diag.primitives import (
    inspect_cgroup_state,
    inspect_mount_state,
    inspect_process_state,
    inspect_service_state,
)
from android_ptc_diag.triage import fixed_triage


CASE_DIR = Path(__file__).parents[1] / "eval" / "cases" / "runtime_service_unready_001"


def _diagnosis() -> DirectDiagnosis:
    return DirectDiagnosis(
        primary_candidate_indices=[1],
        evidence_indices=[1, 2, 24],
        propagation_event_indices=list(range(2, 24)),
        uncertainties=[
            "cross-source order unknown",
            "why the runtime service failed to become ready is not established",
        ],
        recommended_next_checks=[
            "Inspect runtime service readiness and required dependency state",
            "Check runtime process, cgroup, and required mount state",
        ],
    )


def _provider_script(decisions):
    remaining = iter(decisions)

    def provider(diagnosis, returned_evidence):
        return next(remaining)

    return provider


def _request(check: str, uncertainty: str) -> FollowupRequest:
    arguments = {"path": "/container/runtime"} if check == "inspect_mount_state" else {"name": "runtime_service"}
    return FollowupRequest(
        uncertainty=uncertainty,
        check=check,
        arguments=arguments,
        reason=f"Use {check} to narrow the current evidence gap.",
    )


def test_runtime_service_unready_is_deterministic_and_static_reason_is_incomplete():
    request, expected = load_case(CASE_DIR)
    events = ReplayBackend(CASE_DIR).collect_events(request)

    assert request.diagnosis_scope == "container_runtime_boundary"
    assert len(events) == 26
    assert sum(event.source == "logcat" for event in events) == 24
    assert sum(event.source == "kmsg" for event in events) == 2
    assert expected["uncertainties"] == [
        "cross-source order unknown",
        "why the runtime service failed to become ready is not established",
    ]
    assert not any("vendor_bridge" in event.message for event in events)
    assert not any("oom" in event.message.lower() for event in events)
    assert not any("mount" in event.message.lower() for event in events)

    cluster_events(events)
    compact = build_compact_evidence(events)
    assert compact["group_count"] == 8
    assert sorted((group["count"] for group in compact["groups"]), reverse=True)[:3] == [10, 7, 4]
    compact_text = " ".join(group["template"] for group in compact["groups"])
    for phrase in (
        "runtime service startup timeout",
        "required service unavailable",
        "binder transaction failed",
        "app-visible startup dependency unavailable",
        "FATAL EXCEPTION",
    ):
        assert phrase in compact_text

    result = fixed_triage(events, request)
    top3 = result.primary_candidates[:3]
    assert expected["primary_event_contains"] in events[top3[0].event_index].message
    assert len([event for event in events if "binder transaction failed" in event.message]) == 10
    assert len([event for event in events if "required service unavailable" in event.message]) == 7


def test_runtime_state_checks_distinguish_process_cgroup_service_and_mount():
    runtime_state = load_runtime_state(CASE_DIR)

    process = inspect_process_state(runtime_state, name="runtime_service")
    cgroup = inspect_cgroup_state(runtime_state, name="runtime_service")
    service = inspect_service_state(runtime_state, name="runtime_service")
    mount = inspect_mount_state(runtime_state, path="/container/runtime")

    assert process["state"] == "running"
    assert cgroup["oom_kill_count"] == 0
    assert cgroup["memory_current_bytes"] < cgroup["memory_max_bytes"]
    assert service["state"] == "starting"
    assert service["ready"] is False
    assert service["dependency_ready"] is False
    assert mount["available"] is True
    assert all(item["read_only"] is True for item in (process, cgroup, service, mount))


def test_one_step_service_check_narrows_unready_uncertainty():
    initial = _diagnosis()
    updated = initial.model_copy(deep=True)
    updated.uncertainties = ["why vendor_bridge is not ready is not established"]
    result = run_semantic_diagnosis_loop(
        initial,
        load_runtime_state(CASE_DIR),
        _provider_script(
            [
                DiagnosisLoopDecision(
                    diagnosis=initial,
                    followup_request=_request(
                        "inspect_service_state",
                        "why the runtime service failed to become ready is not established",
                    ),
                ),
                DiagnosisLoopDecision(diagnosis=updated),
            ]
        ),
    )

    assert len(result.steps) == 1
    assert result.steps[0].returned_evidence["ready"] is False
    assert result.steps[0].returned_evidence["dependency_ready"] is False
    assert result.current_diagnosis.uncertainties == [
        "why vendor_bridge is not ready is not established"
    ]
    assert result.stop_reason == "model chose to stop"


def test_two_step_process_then_service_path_preserves_unknown_cause():
    initial = _diagnosis()
    narrowed = initial.model_copy(deep=True)
    narrowed.uncertainties = [
        "cross-source order unknown",
        "why the runtime service failed to become ready is not established",
    ]
    final = narrowed.model_copy(deep=True)
    final.uncertainties = [
        "cross-source order unknown",
        "why vendor_bridge is not ready is not established",
    ]
    result = run_semantic_diagnosis_loop(
        initial,
        load_runtime_state(CASE_DIR),
        _provider_script(
            [
                DiagnosisLoopDecision(
                    diagnosis=initial,
                    followup_request=_request(
                        "inspect_process_state",
                        "why the runtime service failed to become ready is not established",
                    ),
                ),
                DiagnosisLoopDecision(
                    diagnosis=narrowed,
                    followup_request=_request(
                        "inspect_service_state",
                        "why the runtime service failed to become ready is not established",
                    ),
                ),
                DiagnosisLoopDecision(diagnosis=final),
            ]
        ),
    )

    assert len(result.steps) == 2
    assert result.steps[0].returned_evidence["state"] == "running"
    assert result.steps[1].returned_evidence["ready"] is False
    assert result.current_diagnosis.uncertainties[-1] == "why vendor_bridge is not ready is not established"
    assert result.stop_reason == "model chose to stop"
