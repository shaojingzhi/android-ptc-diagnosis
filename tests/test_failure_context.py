from datetime import datetime, timedelta
import json

import pytest

from android_ptc_diag.models import LogEvent
from android_ptc_diag.primitives import inspect_failure_context


BASE_TIME = datetime(2026, 10, 14, 10, 0, 0)


def _logcat(
    seconds: float,
    message: str,
    *,
    pid: int = 10,
    tag: str = "AndroidRuntime",
    raw_line_number: int | None = None,
) -> LogEvent:
    return LogEvent(
        source="logcat",
        raw_line_number=raw_line_number,
        timestamp=BASE_TIME + timedelta(seconds=seconds),
        level="E",
        pid=pid,
        tag=tag,
        message=message,
        template_id=message,
        template=message,
    )


def test_crash_candidate_returns_nearby_evidence_with_provenance():
    events = [
        _logcat(-1, "launch activity", raw_line_number=41),
        _logcat(0, "FATAL EXCEPTION: main", raw_line_number=42),
        _logcat(1, "force finishing activity", raw_line_number=43),
        _logcat(20, "unrelated later event", raw_line_number=44),
    ]

    result = inspect_failure_context(events, [1], before_seconds=2, after_seconds=2)

    assert result["candidate_indices"] == [1]
    assert result["context_indices"] == [0, 2]
    assert result["events"][0]["relation"] == "candidate"
    assert result["events"][0]["raw_line_number"] == 42
    assert result["events"][0]["source"] == "logcat"
    assert result["read_only"] is True


def test_same_pid_has_priority_at_the_same_timestamp():
    events = [
        _logcat(0, "candidate", pid=10),
        _logcat(0, "different process", pid=20),
        _logcat(0, "same process", pid=10, tag="ActivityManager"),
    ]

    result = inspect_failure_context(events, [0], max_events=2)

    assert result["context_indices"] == [2]
    assert result["events"][1]["relation"] == "same_pid"


def test_mixed_time_domains_are_kept_source_local():
    events = [
        _logcat(0, "logcat candidate"),
        _logcat(1, "logcat context"),
        LogEvent(
            source="kmsg",
            monotonic_seconds=310.42,
            message="kmsg candidate",
            template_id="k1",
            template="kmsg candidate",
        ),
        LogEvent(
            source="kmsg",
            monotonic_seconds=310.43,
            message="kmsg context",
            template_id="k2",
            template="kmsg context",
        ),
    ]

    result = inspect_failure_context(events, [0, 2], before_seconds=1, after_seconds=1)

    assert set(result["context_indices"]) == {1, 3}
    assert result["cross_source_order"] == "unknown"
    assert result["evidence_summary"]["time_range"] == {"start": None, "end": None}
    assert "cross-source time order is unknown" in result["limitations"]


def test_large_context_is_bounded_and_reports_truncation():
    events = [_logcat(index / 10, f"event {index}") for index in range(101)]

    result = inspect_failure_context(
        events,
        [50],
        before_seconds=5,
        after_seconds=5,
        max_events=7,
    )

    assert len(result["events"]) == 7
    assert len(result["context_indices"]) == 6
    assert result["truncated"] is True
    assert "context truncated by max_events" in result["limitations"]


def test_result_contains_evidence_not_causal_claims():
    result = inspect_failure_context([_logcat(0, "candidate")], [0])
    serialized = json.dumps(result).lower()

    assert "root_cause" not in serialized
    assert "proven_cause" not in serialized
    assert "automatic rca" not in serialized


def test_invalid_candidate_or_bound_is_rejected():
    events = [_logcat(0, "candidate")]

    with pytest.raises(ValueError, match="non-empty"):
        inspect_failure_context(events, [])
    with pytest.raises(ValueError, match="out of range"):
        inspect_failure_context(events, [9])
    with pytest.raises(ValueError, match="at least"):
        inspect_failure_context(events * 2, [0, 1], max_events=1)
