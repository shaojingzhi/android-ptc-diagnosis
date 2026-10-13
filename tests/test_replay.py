from datetime import timedelta
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

import pytest

from android_ptc_diag.backend import AdbCliBackend, AdbCliError, ReplayBackend
from android_ptc_diag.cases import load_case
from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.compact import build_compact_evidence
from android_ptc_diag.primitives import get_events
from android_ptc_diag.models import DirectDiagnosis, TriageRequest
from android_ptc_diag.parsing import parse_logcat
from android_ptc_diag.skills import build_evidence_view
from scripts.generate_binder_flood_case import generate_binder_flood
from android_ptc_diag.triage import fixed_triage


def test_seed_cases_are_replayable():
    root = Path(__file__).parents[1] / "eval" / "cases"
    cases = sorted(p for p in root.iterdir() if p.is_dir())
    assert len(cases) >= 3

    for case_dir in cases:
        request, expected = load_case(case_dir)
        backend = ReplayBackend(case_dir)
        assert backend.collect_logcat(request).strip()
        assert expected["primary_event_contains"]


def test_seed_cases_load_normalized_events():
    root = Path(__file__).parents[1] / "eval" / "cases"

    for case_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        request, expected = load_case(case_dir)
        events = ReplayBackend(case_dir).collect_events(request)
        assert events
        assert {event.source for event in events} >= {"logcat"}
        assert any(expected["primary_event_contains"] in event.message for event in events)

        if (case_dir / "kmsg.txt").exists():
            assert any(event.source == "kmsg" for event in events)


def test_runtime_mount_case_preserves_cross_boundary_evidence():
    case_dir = Path(__file__).parents[1] / "eval" / "cases" / "runtime_mount_failure_001"
    request, expected = load_case(case_dir)
    events = ReplayBackend(case_dir).collect_events(request)

    primary = [event for event in events if expected["primary_event_contains"] in event.message]
    runtime_errors = [
        event
        for event in events
        if event.tag == "RuntimeService" and "runtime service unavailable" in event.message
    ]
    binder_errors = [event for event in events if "binder transaction failed" in event.message]

    assert len(primary) == 1
    assert primary[0].source == "logcat"
    assert {event.source for event in events} == {"logcat", "kmsg"}
    assert len(runtime_errors) == 8
    assert len(binder_errors) == 12
    assert len(runtime_errors) + len(binder_errors) > len(primary)
    assert any(
        event.source == "kmsg" and "containerfs: mount /vendor/firmware failed" in event.message
        for event in events
    )
    assert any("FATAL EXCEPTION" in event.message for event in events)


def test_runtime_mount_case_supports_evidence_driven_diagnosis_contract():
    case_dir = Path(__file__).parents[1] / "eval" / "cases" / "runtime_mount_failure_001"
    request, expected = load_case(case_dir)
    events = ReplayBackend(case_dir).collect_events(request)
    primary_index = next(
        index
        for index, event in enumerate(events)
        if expected["primary_event_contains"] in event.message
    )

    diagnosis = DirectDiagnosis.model_validate(
        {
            "primary_candidate_indices": [primary_index],
            "evidence_indices": [1, 2, 28, 29],
            "propagation_event_indices": list(range(3, 28)),
            "uncertainties": [
                "cross-source order unknown",
                "why the required mount is unavailable is not established",
            ],
            "recommended_next_checks": [
                "Inspect container mount state and required resource availability",
                "Check runtime service and process state after mount setup",
            ],
        }
    )

    output = diagnosis.model_dump()
    assert output["uncertainties"] == [
        "cross-source order unknown",
        "why the required mount is unavailable is not established",
    ]
    assert output["recommended_next_checks"]
    assert "root_cause" not in output


def test_runtime_process_killed_deterministic_characterization():
    case_dir = Path(__file__).parents[1] / "eval" / "cases" / "runtime_process_killed_001"
    request, expected = load_case(case_dir)
    events = ReplayBackend(case_dir).collect_events(request)

    assert request.diagnosis_scope == "container_runtime_boundary"
    assert len(events) == 29
    assert sum(event.source == "logcat" for event in events) == 27
    assert sum(event.source == "kmsg" for event in events) == 2

    cluster_events(events)
    compact = build_compact_evidence(events)
    assert compact["group_count"] == 8
    assert sorted((group["count"] for group in compact["groups"]), reverse=True)[:3] == [
        12,
        8,
        4,
    ]
    compact_text = " ".join(group["template"] for group in compact["groups"])
    for phrase in (
        "memory pressure",
        "runtime service process killed",
        "runtime service process died",
        "required service unavailable",
        "DEAD_OBJECT",
        "app-visible startup dependency unavailable",
        "FATAL EXCEPTION",
    ):
        assert phrase in compact_text

    evidence_view = build_evidence_view(get_events(events)["events"])
    assert evidence_view["ordering"] == "source_local"
    assert evidence_view["cross_source_order"] == "unknown"
    assert [event["monotonic_seconds"] for event in evidence_view["sources"]["kmessage"]] == [
        420.12,
        420.13,
    ]

    result = fixed_triage(events, request)
    top3 = result.primary_candidates[:3]
    assert expected["primary_event_contains"] in events[top3[0].event_index].message
    assert any(
        expected["primary_event_contains"] in events[candidate.event_index].message
        for candidate in top3
    )
    assert expected["uncertainties"] == [
        "cross-source order unknown",
        "why the runtime service process died is not established",
    ]
    assert expected["recommended_next_checks"]


def test_binder_flood_generator_is_deterministic_and_parseable():
    first = generate_binder_flood()
    second = generate_binder_flood()

    assert first == second
    events = parse_logcat("\n".join(first) + "\n", year=2026)
    messages = [event.message for event in events]
    assert len(events) == 500
    assert messages.count("bridge_service terminated unexpectedly") == 1
    assert messages.count("binder transaction failed for bridge_service") == 350


def test_parsers_keep_malformed_lines_as_events():
    from android_ptc_diag.parsing import load_events

    events = load_events("not a standard logcat line\n", "malformed kernel line\n")
    assert [event.message for event in events] == [
        "not a standard logcat line",
        "malformed kernel line",
    ]
    assert [event.source for event in events] == ["logcat", "kmsg"]


def test_logcat_fields_are_normalized():
    from android_ptc_diag.parsing import parse_logcat

    event = parse_logcat(
        "10-03 10:00:02.980 4120 4120 E AndroidRuntime: FATAL EXCEPTION: main\n",
        year=2026,
    )[0]
    assert event.source == "logcat"
    assert event.timestamp is not None
    assert event.timestamp.year == 2026
    assert event.timestamp.tzinfo is None
    assert event.raw_line_number == 1
    assert (event.pid, event.tid, event.level, event.tag) == (
        4120,
        4120,
        "E",
        "AndroidRuntime",
    )


def test_logcat_explicit_device_timezone_produces_aware_timestamp():
    event = parse_logcat(
        "10-13 15:21:28.182 4120 4120 E AndroidRuntime: crash\n",
        year=2026,
        device_timezone="Asia/Shanghai",
    )[0]

    assert event.timestamp is not None
    assert event.timestamp.utcoffset() == timedelta(hours=8)


def test_logcat_buffer_headers_are_not_events_but_line_numbers_are_preserved():
    raw = "\n".join(
        [
            "--------- beginning of main",
            "--------- beginning of kernel",
            "--------- beginning of system",
            "--------- beginning of radio",
            "--------- beginning of events",
            "10-13 15:21:28.182 4120 4120 E AndroidRuntime: crash",
            "malformed but diagnostic",
            "--------- beginning of crash",
        ]
    )

    events = parse_logcat(raw, year=2026, device_timezone=ZoneInfo("Asia/Shanghai"))

    assert [event.message for event in events] == ["crash", "malformed but diagnostic"]
    assert [event.raw_line_number for event in events] == [6, 7]
    assert list(enumerate(event.raw_line_number for event in events)) == [(0, 6), (1, 7)]


def test_kmsg_monotonic_timestamp_is_preserved():
    from android_ptc_diag.parsing import parse_kmsg

    event = parse_kmsg("[ 222.000000] lowmemorykiller: killed process 3300\n")[0]
    assert event.source == "kmsg"
    assert event.monotonic_seconds == 222.0
    assert event.message == "lowmemorykiller: killed process 3300"


def test_adb_cli_backend_collects_raw_logcat_and_normalizes(monkeypatch):
    calls = []
    raw_logcat = "10-03 10:00:02.980 4120 4120 E AndroidRuntime: FATAL EXCEPTION: main\n"

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=raw_logcat, stderr="")

    monkeypatch.setattr("android_ptc_diag.backend.subprocess.run", fake_run)
    backend = AdbCliBackend(device="emulator-5554", log_lines=200)
    events = backend.collect_events(TriageRequest(symptom="crash", target_package="com.demo.crash"))

    assert calls == [["adb-agent", "--device", "emulator-5554", "system", "logcat", "--lines", "200"]]
    assert events[0].tag == "AndroidRuntime"
    assert events[0].message == "FATAL EXCEPTION: main"


def test_adb_cli_backend_exposes_flow_commands(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("android_ptc_diag.backend.subprocess.run", fake_run)
    backend = AdbCliBackend(device="emulator-5554")
    backend.doctor()
    backend.clear_logcat()
    backend.launch_app("com.demo.crash")

    assert calls == [
        ["adb-agent", "--device", "emulator-5554", "doctor"],
        ["adb-agent", "--device", "emulator-5554", "system", "clear-logcat"],
        ["adb-agent", "--device", "emulator-5554", "app", "launch", "com.demo.crash"],
    ]


def test_adb_cli_backend_reports_cli_failure(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="device offline")

    monkeypatch.setattr("android_ptc_diag.backend.subprocess.run", fake_run)

    with pytest.raises(AdbCliError, match="device offline"):
        AdbCliBackend().doctor()
