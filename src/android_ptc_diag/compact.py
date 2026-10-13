"""Deterministic, loss-aware compact evidence derived from normalized events."""

from __future__ import annotations

from typing import Any

from .clustering import group_events_by_identity
from .models import LogEvent


_MODEL_GROUP_FIELDS = (
    "group_id",
    "template_id",
    "template",
    "count",
    "first_index",
    "last_index",
    "representative_index",
    "source",
    "level",
    "tag",
    "pid",
)

_COMPARISON_GROUP_FIELDS = (
    "baseline_count",
    "incident_count",
    "unassigned_count",
    "not_seen_in_observed_baseline",
)


def build_compact_evidence(
    events: list[LogEvent],
    *,
    baseline_indices: list[int] | None = None,
    incident_indices: list[int] | None = None,
) -> dict[str, Any]:
    """Group events without changing raw provenance.

    Comparison counts are added only when either index set is supplied. Events
    in neither explicit set remain unassigned rather than becoming baseline.
    """
    comparison_requested = baseline_indices is not None or incident_indices is not None
    baseline_set = set(baseline_indices or [])
    incident_set = set(incident_indices or [])
    for name, selected in (("baseline", baseline_set), ("incident", incident_set)):
        if any(index < 0 or index >= len(events) for index in selected):
            raise ValueError(f"{name} event index out of range")
    overlap = baseline_set & incident_set
    if overlap:
        raise ValueError(f"baseline and incident indices overlap: {sorted(overlap)}")

    groups = group_events_by_identity(events)
    for group in groups:
        indices = group["indices"]
        if comparison_requested:
            baseline_count = sum(index in baseline_set for index in indices)
            incident_count = sum(index in incident_set for index in indices)
            unassigned_count = len(indices) - baseline_count - incident_count
            group.update(
                {
                    "baseline_count": baseline_count,
                    "incident_count": incident_count,
                    "unassigned_count": unassigned_count,
                    "not_seen_in_observed_baseline": (
                        incident_count > 0 and baseline_count == 0
                    ),
                }
            )
    return {"event_count": len(events), "group_count": len(groups), "groups": groups}


def compact_model_view(compact: dict[str, Any]) -> dict[str, Any]:
    """Return all group summaries while excluding full provenance index arrays."""
    groups = [
        {
            field: group[field]
            for field in _MODEL_GROUP_FIELDS + _COMPARISON_GROUP_FIELDS
            if field in group
        }
        for group in compact["groups"]
    ]
    return {
        "event_count": compact["event_count"],
        "group_count": len(groups),
        "groups": groups,
    }


def evidence_recall(compact: dict[str, Any], evidence_indices: list[int]) -> float:
    """Measure whether expected evidence remains reachable through provenance."""
    expected = set(evidence_indices)
    if not expected:
        return 1.0
    reachable = {
        index
        for group in compact["groups"]
        for index in group["indices"]
    }
    return len(expected & reachable) / len(expected)
