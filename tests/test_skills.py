from pathlib import Path

from android_ptc_diag.backend import ReplayBackend
from android_ptc_diag.cases import load_case
from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.compact import build_compact_evidence
from android_ptc_diag.skills import build_timeline, inspect_group_evidence


CASE_DIR = Path(__file__).parents[1] / "eval" / "cases" / "binder_flood_001"


def _fixture():
    request, _ = load_case(CASE_DIR)
    events = ReplayBackend(CASE_DIR).collect_events(request)
    cluster_events(events)
    compact = build_compact_evidence(events)
    return events, compact["groups"]


def _group(groups, index):
    return next(group for group in groups if index in group["indices"])


def test_large_group_inspection_remains_bounded():
    events, groups = _fixture()
    binder = _group(groups, 21)

    bundle = inspect_group_evidence(events, [binder], boundary_radius=1)

    assert binder["count"] == 350
    assert len(bundle["events"]) < binder["count"]
    assert bundle["selected_indices"] == sorted(set(bundle["selected_indices"]))
    assert all(0 <= index < len(events) for index in bundle["selected_indices"])
    assert {21, 370} <= set(bundle["selected_indices"])


def test_singleton_representative_is_selected_once():
    events, groups = _fixture()
    root = _group(groups, 20)

    bundle = inspect_group_evidence(events, [root], boundary_radius=0)

    assert bundle["selected_indices"] == [20]
    assert [event["index"] for event in bundle["events"]] == [20]


def test_multiple_groups_are_deduplicated_sorted_and_not_fully_expanded():
    events, groups = _fixture()
    selected_groups = [_group(groups, index) for index in (20, 21, 371, 499)]

    bundle = inspect_group_evidence(events, selected_groups, boundary_radius=1)

    assert bundle["selected_indices"] == [
        19,
        20,
        21,
        22,
        369,
        370,
        371,
        372,
        449,
        450,
        451,
        498,
        499,
    ]
    assert len(bundle["events"]) == 13
    assert sum(group["count"] for group in selected_groups) == 432


def test_boundary_expansion_clamps_to_event_range():
    events, groups = _fixture()
    edge_groups = [_group(groups, 0), _group(groups, len(events) - 1)]

    bundle = inspect_group_evidence(events, edge_groups, boundary_radius=2)

    assert min(bundle["selected_indices"]) == 0
    assert max(bundle["selected_indices"]) == len(events) - 1
    assert all(0 <= index < len(events) for index in bundle["selected_indices"])


def test_skill_composition_is_deterministic():
    events, groups = _fixture()
    selected_groups = [_group(groups, index) for index in (20, 21, 371, 499)]

    first = inspect_group_evidence(events, selected_groups, boundary_radius=1)
    second = inspect_group_evidence(events, selected_groups, boundary_radius=1)

    assert first == second
    assert build_timeline(first["events"]) == build_timeline(second["events"])


def test_timeline_keeps_indices_fields_and_removes_duplicate_events():
    events, groups = _fixture()
    bundle = inspect_group_evidence(events, [_group(groups, 21)], boundary_radius=1)
    selected = list(reversed(bundle["events"])) + [bundle["events"][0]]

    timeline = build_timeline(selected)

    assert [entry["index"] for entry in timeline] == bundle["selected_indices"]
    assert len(timeline) == len(bundle["selected_indices"])
    assert all("source" in entry and "message" in entry for entry in timeline)


def test_timeline_falls_back_to_index_when_timestamps_are_missing():
    selected = [
        {"index": 2, "timestamp": None, "source": "kmsg", "message": "later"},
        {"index": 1, "timestamp": "2026-10-03T11:20:00", "source": "logcat", "message": "first"},
    ]

    assert [entry["index"] for entry in build_timeline(selected)] == [1, 2]
