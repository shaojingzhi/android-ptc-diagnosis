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
