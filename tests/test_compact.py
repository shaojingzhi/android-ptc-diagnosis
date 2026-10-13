import json
from pathlib import Path

from android_ptc_diag.backend import ReplayBackend
from android_ptc_diag.cases import load_case
from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.compact import (
    build_compact_evidence,
    compact_model_view,
    evidence_recall,
)
from android_ptc_diag.execution import CompactDirectExecutor, MAX_TOOL_ROUNDS
from android_ptc_diag.models import LogEvent
from android_ptc_diag.primitives import group_by_template
from scripts.eval_compact_direct import _tool_metrics


CASE_DIR = Path(__file__).parents[1] / "eval" / "cases" / "binder_flood_001"


def _clustered_events():
    request, _ = load_case(CASE_DIR)
    events = ReplayBackend(CASE_DIR).collect_events(request)
    cluster_events(events)
    return events


def _group_with_index(compact, index):
    return next(group for group in compact["groups"] if index in group["indices"])


def test_compact_evidence_preserves_every_raw_event_once():
    events = _clustered_events()
    before = [event.model_dump() for event in events]

    compact = build_compact_evidence(events)
    owned_indices = [index for group in compact["groups"] for index in group["indices"]]

    assert compact["event_count"] == 500
    assert compact["group_count"] == 13
    assert sorted(owned_indices) == list(range(500))
    assert len(owned_indices) == len(set(owned_indices))
    assert [event.model_dump() for event in events] == before
    assert evidence_recall(compact, [20, 21, 371, 499]) == 1.0


def test_compact_evidence_preserves_singleton_and_duplicate_provenance():
    compact = build_compact_evidence(_clustered_events())

    root = _group_with_index(compact, 20)
    assert root["count"] == 1
    assert root["first_index"] == 20
    assert root["last_index"] == 20
    assert root["representative_index"] == 20

    binder = _group_with_index(compact, 21)
    assert binder["count"] == 350
    assert binder["first_index"] == 21
    assert binder["last_index"] == 370
    assert len(binder["indices"]) == 350

    unavailable = _group_with_index(compact, 371)
    assert unavailable["count"] == 80
    assert unavailable["first_index"] == 371
    assert unavailable["last_index"] == 450
    assert len(unavailable["indices"]) == 80


def test_compact_model_view_is_deterministic_and_omits_provenance_arrays():
    events = _clustered_events()
    first = compact_model_view(build_compact_evidence(events))
    second = compact_model_view(build_compact_evidence(events))

    assert first == second
    assert first["group_count"] == 13
    assert [group["group_id"] for group in first["groups"]] == list(range(13))
    assert [group["first_index"] for group in first["groups"]] == sorted(
        group["first_index"] for group in first["groups"]
    )
    assert all("indices" not in group for group in first["groups"])
    assert any(group["first_index"] == 20 and group["count"] == 1 for group in first["groups"])

    raw_bytes = len(
        json.dumps(
            [event.model_dump(mode="json") for event in events],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    model_view_bytes = len(
        json.dumps(first, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    assert model_view_bytes < raw_bytes


def test_compact_group_ids_are_stable_group_identity_not_event_indices():
    compact = build_compact_evidence(_clustered_events())

    for group_id, group in enumerate(compact["groups"]):
        assert group["group_id"] == group_id
    binder = _group_with_index(compact, 21)
    assert binder["group_id"] != binder["representative_index"]

    view = compact_model_view(compact)
    assert [group["group_id"] for group in view["groups"]] == list(range(13))
    assert all("indices" not in group for group in view["groups"])


def test_group_identity_separates_source_tag_and_level_and_matches_primitive():
    events = [
        LogEvent(
            source="logcat",
            tag="AndroidRuntime",
            level="E",
            pid=10,
            message="at x.y(Z.java:1)",
        ),
        LogEvent(
            source="logcat",
            tag="AndroidRuntime",
            level="E",
            pid=11,
            message="at x.y(Z.java:2)",
        ),
        LogEvent(
            source="logcat",
            tag="Speech",
            level="E",
            pid=12,
            message="at x.y(Z.java:3)",
        ),
        LogEvent(
            source="logcat",
            tag="AndroidRuntime",
            level="W",
            pid=10,
            message="at x.y(Z.java:4)",
        ),
        LogEvent(
            source="replay",
            tag="AndroidRuntime",
            level="E",
            pid=10,
            message="at x.y(Z.java:5)",
        ),
    ]
    cluster_events(events)

    compact = build_compact_evidence(events)
    primitive = group_by_template(events)

    assert len({event.template_id for event in events}) == 1
    assert compact["groups"] == primitive["groups"]
    assert [group["count"] for group in compact["groups"]] == [2, 1, 1, 1]
    assert [event.repeat_count for event in events] == [2, 2, 1, 1, 1]
    assert compact["groups"][0]["pid"] is None
    assert [group["representative_index"] for group in compact["groups"]] == [0, 2, 3, 4]
    owned = [index for group in compact["groups"] for index in group["indices"]]
    assert sorted(owned) == list(range(len(events)))
    assert len(owned) == len(set(owned))


def test_missing_group_metadata_keeps_unclustered_events_separate():
    events = [
        LogEvent(source="logcat", message="malformed line"),
        LogEvent(source="logcat", message="malformed line"),
    ]

    groups = build_compact_evidence(events)["groups"]

    assert len(groups) == 2
    assert [group["indices"] for group in groups] == [[0], [1]]
    assert all(group["tag"] is None and group["level"] is None for group in groups)


def test_compact_direct_prompt_contains_all_groups_without_ground_truth_labels():
    events = _clustered_events()
    request, _ = load_case(CASE_DIR)
    executor = CompactDirectExecutor(object(), events)

    prompt = executor._request_prompt(request)
    payload = json.loads(prompt.rsplit("\n", 1)[-1])
    groups = payload["compact_evidence"]["groups"]

    assert payload["raw_event_count"] == 500
    assert payload["compact_group_count"] == 13
    assert "incident_context" not in payload
    assert "observed_behavior" not in payload
    assert len(groups) == 13
    assert all("indices" not in group for group in groups)
    assert "bridge_service terminated unexpectedly" in prompt
    assert "ground truth" not in prompt.lower()
    assert "expected primary" not in prompt.lower()
    assert "primary index 20" not in prompt.lower()
    assert executor.max_tool_rounds == MAX_TOOL_ROUNDS == 6
    assert len(executor.tools) == 9


def test_compact_direct_prompt_adds_explicit_incident_context_only_when_supplied():
    events = [
        LogEvent(source="logcat", message="baseline", template_id="a", template="baseline"),
        LogEvent(source="logcat", message="incident", template_id="b", template="incident"),
        LogEvent(source="logcat", message="unassigned", template_id="c", template="unassigned"),
    ]
    request = load_case(CASE_DIR)[0].model_copy(
        update={
            "target_component": "MainActivity",
            "observed_behavior": "Activity crashes during startup",
        }
    )
    executor = CompactDirectExecutor(
        object(), events, baseline_indices=[0], incident_indices=[1]
    )

    payload = json.loads(executor._request_prompt(request).rsplit("\n", 1)[-1])

    assert payload["target_component"] == "MainActivity"
    assert payload["observed_behavior"] == "Activity crashes during startup"
    assert payload["incident_context"] == {
        "incident_time": None,
        "baseline_event_count": 1,
        "incident_event_count": 1,
        "unassigned_event_count": 1,
    }
    assert payload["compact_evidence"]["groups"][0]["baseline_count"] == 1
    assert payload["compact_evidence"]["groups"][1]["incident_count"] == 1


def test_compact_direct_tool_metrics_distinguish_full_and_indexed_reads():
    details = [
        {
            "tool": "get_events",
            "arguments": {},
            "record_count": 500,
            "result_bytes": 150_000,
        },
        {
            "tool": "get_events",
            "arguments": {"indices": [20, 21]},
            "record_count": 2,
            "result_bytes": 700,
        },
        {
            "tool": "filter_by_text",
            "arguments": {"text": "fatal"},
            "record_count": 1,
            "result_bytes": 30,
        },
    ]

    assert _tool_metrics(details) == {
        "get_events_total_calls": 2,
        "get_events_full_calls": 1,
        "get_events_indexed_calls": 1,
        "largest_get_events_record_count": 500,
        "largest_tool_result_bytes": 150_000,
    }
