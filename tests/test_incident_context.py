from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from android_ptc_diag.cases import load_case, load_case_metadata
from android_ptc_diag.compact import build_compact_evidence, compact_model_view
from android_ptc_diag.models import CaseMetadata, LogEvent, TriageRequest
from android_ptc_diag.triage import incident_event_indices


CASES = Path(__file__).parents[1] / "eval" / "cases"


def test_incident_window_selects_only_wall_clock_events_inside_window():
    center = datetime(2026, 10, 13, 12, 0, 0)
    events = [
        LogEvent(source="logcat", timestamp=center - timedelta(seconds=6), message="before"),
        LogEvent(source="logcat", timestamp=center - timedelta(seconds=5), message="start"),
        LogEvent(source="logcat", timestamp=center + timedelta(seconds=3), message="end"),
        LogEvent(source="logcat", timestamp=center + timedelta(seconds=4), message="after"),
        LogEvent(source="kmsg", monotonic_seconds=10.0, message="kernel event"),
        LogEvent(
            source="kmsg",
            timestamp=center.replace(tzinfo=timezone.utc),
            monotonic_seconds=11.0,
            message="unmapped",
        ),
    ]
    request = TriageRequest(
        symptom="service unavailable",
        target_component="runtime_service",
        incident_time=center,
        window_before_seconds=5,
        window_after_seconds=3,
        expected_behavior="service becomes ready",
        observed_behavior="service remains unavailable",
    )

    assert incident_event_indices(events, request) == [1, 2]
    assert request.target_component == "runtime_service"
    assert request.expected_behavior == "service becomes ready"
    assert request.observed_behavior == "service remains unavailable"


def test_failure_timestamp_remains_an_incident_time_fallback():
    failure_time = datetime(2026, 10, 13, 12, 0, 0)
    events = [LogEvent(source="logcat", timestamp=failure_time, message="failure")]
    request = TriageRequest(
        symptom="failure",
        failure_timestamp=failure_time,
        window_before_seconds=0,
    )

    assert incident_event_indices(events, request) == [0]


def test_incident_time_without_window_does_not_imply_zero_width_selection():
    incident_time = datetime(2026, 10, 13, 12, 0, 0)
    events = [LogEvent(source="logcat", timestamp=incident_time, message="exact")]
    request = TriageRequest(symptom="failure", incident_time=incident_time)

    assert incident_event_indices(events, request) == []


@pytest.mark.parametrize(
    ("center", "event_time"),
    [
        (
            datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 13, 12, 0),
        ),
        (
            datetime(2026, 10, 13, 12, 0),
            datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc),
        ),
    ],
)
def test_incident_window_rejects_mixed_datetime_awareness(center, event_time):
    events = [LogEvent(source="logcat", timestamp=event_time, message="event")]
    request = TriageRequest(
        symptom="failure",
        incident_time=center,
        window_before_seconds=1,
        window_after_seconds=1,
    )

    with pytest.raises(ValueError, match="same timezone awareness"):
        incident_event_indices(events, request)


def test_old_cases_load_without_incident_context():
    request, expected = load_case(CASES / "java_crash_001")

    assert request.failure_timestamp is not None
    assert request.incident_time is None
    assert request.target_component is None
    assert request.expected_behavior is None
    assert request.observed_behavior is None
    assert expected["primary_event_contains"] == "FATAL EXCEPTION"


def test_case_provenance_distinguishes_synthetic_and_local_real_log():
    synthetic = load_case_metadata(CASES / "runtime_service_unready_001")
    local_real = CaseMetadata(provenance="locally_reproduced_real_log")

    assert synthetic.provenance == "hand_authored_synthetic"
    assert local_real.provenance == "locally_reproduced_real_log"
    assert synthetic.provenance != local_real.provenance


def test_compact_incident_comparison_counts_are_deterministic():
    events = [
        LogEvent(source="logcat", message="A baseline", template_id="a", template="A"),
        LogEvent(source="logcat", message="B incident", template_id="b", template="B"),
        LogEvent(source="logcat", message="A incident", template_id="a", template="A"),
        LogEvent(source="logcat", message="C unassigned", template_id="c", template="C"),
        LogEvent(source="kmsg", message="K unassigned", template_id="k", template="K"),
    ]

    first = build_compact_evidence(
        events,
        baseline_indices=[0],
        incident_indices=[1, 2],
    )
    second = build_compact_evidence(
        events,
        baseline_indices=[0],
        incident_indices=[1, 2],
    )

    assert first == second
    counts = {
        group["template_id"]: (
            group["baseline_count"],
            group["incident_count"],
            group["unassigned_count"],
            group["not_seen_in_observed_baseline"],
        )
        for group in first["groups"]
    }
    assert counts == {
        "a": (1, 1, 0, False),
        "b": (0, 1, 0, True),
        "c": (0, 0, 1, False),
        "k": (0, 0, 1, False),
    }
    kmsg_group = next(group for group in first["groups"] if group["template_id"] == "k")
    assert kmsg_group["indices"] == [4]
    assert all(
        field in compact_model_view(first)["groups"][0]
        for field in (
            "baseline_count",
            "incident_count",
            "unassigned_count",
            "not_seen_in_observed_baseline",
        )
    )


def test_incident_indices_do_not_make_other_events_baseline():
    events = [
        LogEvent(source="logcat", message="outside", template_id="outside"),
        LogEvent(source="logcat", message="incident", template_id="incident"),
    ]

    compact = build_compact_evidence(events, incident_indices=[1])
    outside = next(group for group in compact["groups"] if group["template_id"] == "outside")

    assert outside["baseline_count"] == 0
    assert outside["incident_count"] == 0
    assert outside["unassigned_count"] == 1


def test_baseline_and_incident_indices_must_not_overlap():
    events = [LogEvent(source="logcat", message="event")]

    with pytest.raises(ValueError, match="indices overlap"):
        build_compact_evidence(events, baseline_indices=[0], incident_indices=[0])


def test_compact_without_incident_context_keeps_frozen_group_shape():
    compact = build_compact_evidence(
        [LogEvent(source="logcat", message="A", template_id="a", template="A")]
    )

    group = compact_model_view(compact)["groups"][0]
    for field in (
        "baseline_count",
        "incident_count",
        "unassigned_count",
        "not_seen_in_observed_baseline",
    ):
        assert field not in group
