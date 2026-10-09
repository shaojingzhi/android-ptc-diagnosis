from pathlib import Path
import subprocess

import pytest

from android_ptc_diag.backend import AdbCliBackend, AdbCliError, ReplayBackend
from android_ptc_diag.cases import load_case
from android_ptc_diag.models import TriageRequest
from android_ptc_diag.parsing import parse_logcat
from scripts.generate_binder_flood_case import generate_binder_flood


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
    assert (event.pid, event.tid, event.level, event.tag) == (
        4120,
        4120,
        "E",
        "AndroidRuntime",
    )


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
