"""Deterministic, loss-aware compact evidence derived from normalized events."""

from __future__ import annotations

from typing import Any

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


def build_compact_evidence(events: list[LogEvent]) -> dict[str, Any]:
    """Group events by existing template metadata without changing raw events."""
    groups_by_key: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(events):
        key = event.template_id or f"unclustered:{index}"
        group = groups_by_key.setdefault(
            key,
            {
                "template_id": event.template_id,
                "template": event.template or event.message,
                "indices": [],
            },
        )
        group["indices"].append(index)

    groups: list[dict[str, Any]] = []
    for group in groups_by_key.values():
        indices = group["indices"]
        first_index = min(indices)
        representative = events[first_index]
        group.update(
            {
                "count": len(indices),
                "first_index": first_index,
                "last_index": max(indices),
                "representative_index": first_index,
                "source": representative.source,
                "level": representative.level,
                "tag": representative.tag,
                "pid": representative.pid,
            }
        )
        groups.append(group)

    groups.sort(key=lambda group: group["first_index"])
    for group_id, group in enumerate(groups):
        group["group_id"] = group_id
    return {"event_count": len(events), "group_count": len(groups), "groups": groups}


def compact_model_view(compact: dict[str, Any]) -> dict[str, Any]:
    """Return all group summaries while excluding full provenance index arrays."""
    groups = [
        {field: group[field] for field in _MODEL_GROUP_FIELDS}
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
