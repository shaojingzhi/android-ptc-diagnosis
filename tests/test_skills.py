from pathlib import Path

from android_ptc_diag.backend import ReplayBackend
from android_ptc_diag.cases import load_case
from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.compact import build_compact_evidence
from android_ptc_diag.primitives import get_events
from android_ptc_diag.skills import build_timeline, select_group_evidence_indices


CASE_DIR = Path(__file__).parents[1] / "eval" / "cases" / "binder_flood_001"


def _fixture():
    request, _ = load_case(CASE_DIR)
    events = ReplayBackend(CASE_DIR).collect_events(request)
    cluster_events(events)
    compact = build_compact_evidence(events)
    return events, compact["groups"]


def _group(groups, index):
    return next(group for group in groups if index in group["indices"])


def test_large_group_selection_remains_bounded():
    events, groups = _fixture()
    binder = _group(groups, 21)

    selected = select_group_evidence_indices([binder], len(events), boundary_radius=1)

    assert binder["count"] == 350
    assert selected == [20, 21, 22, 369, 370, 371]
    assert len(selected) < binder["count"]


def test_singleton_representative_is_selected_once():
    events, groups = _fixture()
    root = _group(groups, 20)

    selected = select_group_evidence_indices([root], len(events), boundary_radius=1)

    assert selected == [19, 20, 21]
    assert selected.count(20) == 1


def test_multiple_groups_are_deduplicated_sorted_and_not_fully_expanded():
    events, groups = _fixture()
    selected_groups = [_group(groups, index) for index in (20, 21, 371, 499)]

    selected = select_group_evidence_indices(
        selected_groups, len(events), boundary_radius=1
    )

    assert selected == [
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
    assert len(selected) == 13
    assert sum(group["count"] for group in selected_groups) == 432


def test_boundary_expansion_clamps_to_event_range():
    edge_groups = [
        {"representative_index": 0, "first_index": 0, "last_index": 0},
        {"representative_index": 499, "first_index": 499, "last_index": 499},
    ]

    selected = select_group_evidence_indices(edge_groups, 500, boundary_radius=2)

    assert selected == [0, 1, 2, 497, 498, 499]


def test_selection_is_deterministic():
    events, groups = _fixture()
    selected_groups = [_group(groups, index) for index in (20, 21, 371, 499)]

    first = select_group_evidence_indices(
        selected_groups, len(events), boundary_radius=1
    )
    second = select_group_evidence_indices(
        selected_groups, len(events), boundary_radius=1
    )

    assert first == second


def test_selection_does_not_require_group_provenance_indices():
    group = {
        "count": 100_000,
        "first_index": 10,
        "last_index": 999,
        "representative_index": 10,
    }

    assert select_group_evidence_indices([group], 1_000, boundary_radius=1) == [
        9,
        10,
        11,
        998,
        999,
    ]


def test_timeline_keeps_indices_fields_and_removes_duplicate_events():
    events, groups = _fixture()
    selected_indices = select_group_evidence_indices(
        [_group(groups, 21)], len(events), boundary_radius=1
    )
    materialized = get_events(events, selected_indices)["events"]
    selected = list(reversed(materialized)) + [materialized[0]]

    timeline = build_timeline(selected)

    assert [entry["index"] for entry in timeline] == selected_indices
    assert len(timeline) == len(selected_indices)
    assert all("source" in entry and "message" in entry for entry in timeline)


def test_timeline_falls_back_to_index_when_timestamps_are_missing():
    selected = [
        {"index": 2, "timestamp": None, "source": "kmsg", "message": "later"},
        {"index": 1, "timestamp": "2026-10-03T11:20:00", "source": "logcat", "message": "first"},
    ]

    assert [entry["index"] for entry in build_timeline(selected)] == [1, 2]
