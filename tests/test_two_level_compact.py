import json
from datetime import datetime, timedelta, timezone

import pytest

from android_ptc_diag.compact import (
    build_two_level_evidence_index,
    query_deferred_groups,
    two_level_model_view,
)
from android_ptc_diag.execution import CompactDirectExecutor, TwoLevelCompactDirectExecutor
from android_ptc_diag.models import LogEvent, TriageRequest


def _fixture():
    start = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    events = [
        LogEvent(
            source="logcat",
            timestamp=start - timedelta(seconds=5),
            level="I",
            tag="IdleService",
            message="steady state",
            template_id="idle",
            template="steady state",
        ),
        LogEvent(
            source="logcat",
            timestamp=start,
            level="W",
            tag="RuntimeService",
            message="target.pkg startup delayed",
            template_id="delay",
            template="target.pkg startup delayed",
        ),
        LogEvent(
            source="logcat",
            timestamp=start + timedelta(milliseconds=2),
            level="D",
            tag="RuntimeService",
            message="dependency state changed",
            template_id="dependency",
            template="dependency state changed",
        ),
        LogEvent(
            source="kmsg",
            monotonic_seconds=4.2,
            message="runtime boundary signal",
            template_id="kernel",
            template="runtime boundary signal",
        ),
        LogEvent(
            source="logcat",
            timestamp=start + timedelta(seconds=8),
            level="E",
            tag="OtherComponent",
            message="unrelated failure",
            template_id="other",
            template="unrelated failure",
        ),
    ]
    request = TriageRequest(
        symptom="target.pkg startup failed",
        target_package="target.pkg",
        target_component="RuntimeService",
        incident_time=start,
    )
    return events, request


def _all_groups(index):
    return index["detailed_groups"] + index["deferred_groups"]


def test_two_level_selection_is_bounded_deterministic_and_records_reasons():
    events, request = _fixture()

    first = build_two_level_evidence_index(
        events,
        request,
        baseline_indices=[0],
        incident_indices=[1, 2],
        detailed_group_limit=2,
    )
    second = build_two_level_evidence_index(
        events,
        request,
        baseline_indices=[0],
        incident_indices=[1, 2],
        detailed_group_limit=2,
    )

    assert first == second
    assert len(first["detailed_groups"]) == 2
    assert len(first["deferred_groups"]) == 3
    assert all(group["selection_reasons"] for group in first["detailed_groups"])
    reasons = {
        reason for group in first["detailed_groups"] for reason in group["selection_reasons"]
    }
    assert reasons == {
        "request_overlap",
        "incident_phase",
        "incident_time_proximity",
        "source_tag_level_diversity",
    }


def test_two_level_partition_preserves_global_ids_and_all_provenance():
    events, request = _fixture()
    index = build_two_level_evidence_index(
        events, request, incident_indices=[1, 2], detailed_group_limit=2
    )

    groups = _all_groups(index)
    assert sorted(group["group_id"] for group in groups) == list(range(5))
    owned = [event_index for group in groups for event_index in group["indices"]]
    assert sorted(owned) == list(range(len(events)))
    assert len(owned) == len(set(owned))
    assert all("indices" not in group for group in two_level_model_view(index)["detailed_groups"])


def test_deferred_query_supports_filters_and_keeps_global_group_ids():
    events, request = _fixture()
    index = build_two_level_evidence_index(
        events, request, incident_indices=[1, 2], detailed_group_limit=1
    )
    before = json.dumps(index, sort_keys=True)
    deferred = index["deferred_groups"]
    target = next(group for group in deferred if group["template_id"] == "dependency")

    by_id = query_deferred_groups(index, group_ids=[target["group_id"]])
    by_text = query_deferred_groups(index, text="dependency")
    by_tag = query_deferred_groups(index, tag="RuntimeService")
    by_source = query_deferred_groups(index, source="kmsg")
    by_phase = query_deferred_groups(index, phase="incident")

    assert by_id["groups"][0]["group_id"] == target["group_id"]
    assert by_id["groups"][0]["indices"] == target["indices"]
    assert by_text["groups"][0]["group_id"] == target["group_id"]
    assert target["group_id"] in [group["group_id"] for group in by_tag["groups"]]
    assert by_source["groups"][0]["source"] == "kmsg"
    assert all(group["incident_count"] > 0 for group in by_phase["groups"])
    assert json.dumps(index, sort_keys=True) == before
    with pytest.raises(ValueError, match="known global group IDs"):
        query_deferred_groups(index, group_ids=[999])


def test_deferred_query_enforces_result_and_serialized_byte_limits():
    events, request = _fixture()
    index = build_two_level_evidence_index(
        events, request, incident_indices=[1, 2], detailed_group_limit=1
    )

    result = query_deferred_groups(index, limit=1, max_bytes=512)
    serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()

    assert result["count"] <= 1
    assert len(serialized) <= 512
    assert result["matched_count"] == 4
    assert result["truncated"] is True
    with pytest.raises(ValueError, match="limit"):
        query_deferred_groups(index, limit=21)
    with pytest.raises(ValueError, match="max_bytes"):
        query_deferred_groups(index, max_bytes=100)

    without_phases = build_two_level_evidence_index(events, request, detailed_group_limit=1)
    with pytest.raises(ValueError, match="explicit baseline/incident indices"):
        query_deferred_groups(without_phases, phase="incident")


def test_deferred_groups_are_discoverable_after_level_one_miss():
    events, request = _fixture()
    index = build_two_level_evidence_index(
        events, request, incident_indices=[1, 2], detailed_group_limit=1
    )
    deferred_ids = {group["group_id"] for group in index["deferred_groups"]}

    result = query_deferred_groups(index, text="runtime boundary")

    assert result["count"] == 1
    assert result["groups"][0]["group_id"] in deferred_ids
    assert result["groups"][0]["source"] == "kmsg"


def test_deferred_query_returns_only_sanitized_compact_text():
    events, request = _fixture()
    events[3].message = "private raw value that must not be returned"
    events[3].template = "<redacted runtime boundary signal>"
    index = build_two_level_evidence_index(
        events, request, incident_indices=[1, 2], detailed_group_limit=1
    )

    result = query_deferred_groups(index, text="redacted runtime")
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["count"] == 1
    assert "<redacted runtime boundary signal>" in serialized
    assert "private raw value" not in serialized


def test_two_level_executor_is_opt_in_and_default_contract_is_unchanged():
    events, request = _fixture()
    default = CompactDirectExecutor(object(), events, incident_indices=[1, 2])
    two_level = TwoLevelCompactDirectExecutor(
        object(), events, incident_indices=[1, 2], detailed_group_limit=2
    )

    default_payload = json.loads(default._request_prompt(request).rsplit("\n", 1)[-1])
    prompt = two_level._request_prompt(request)
    payload = json.loads(prompt.rsplit("\n", 1)[-1])

    assert "compact_evidence" in default_payload
    assert "two_level_evidence" not in default_payload
    assert len(default.tools) == 9
    assert "two_level_evidence" in payload
    assert "compact_evidence" not in payload
    assert payload["two_level_evidence"]["deferred_group_count"] == 3
    assert len(two_level.tools) == 10
    assert two_level.tools[-1]["function"]["name"] == "query_deferred_groups"
    assert "query_deferred_groups" in two_level.handlers
