"""Deterministic, loss-aware compact evidence derived from normalized events."""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any

from .clustering import group_events_by_identity
from .models import LogEvent, TriageRequest


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

DEFAULT_DETAILED_GROUP_LIMIT = 32
MAX_GROUP_QUERY_RESULTS = 20
MAX_GROUP_QUERY_BYTES = 16_000


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
    groups = [_model_group(group) for group in compact["groups"]]
    return {
        "event_count": compact["event_count"],
        "group_count": len(groups),
        "groups": groups,
    }


def _model_group(group: dict[str, Any]) -> dict[str, Any]:
    return {
        field: group[field]
        for field in _MODEL_GROUP_FIELDS + _COMPARISON_GROUP_FIELDS
        if field in group
    }


def _request_terms(request: TriageRequest) -> set[str]:
    values = (
        request.symptom,
        request.target_package,
        request.target_component,
        request.expected_behavior,
        request.observed_behavior,
    )
    return {
        token.casefold()
        for value in values
        if value
        for token in re.findall(r"[A-Za-z0-9_.:/-]+", value)
        if len(token) >= 4
    }


def _group_time_distance(
    group: dict[str, Any], events: list[LogEvent], incident_time: datetime | None
) -> float:
    if incident_time is None:
        return float("inf")
    distances = []
    for index in group["indices"]:
        event = events[index]
        if event.source == "kmsg" or event.timestamp is None:
            continue
        if (event.timestamp.tzinfo is None) != (incident_time.tzinfo is None):
            continue
        distances.append(abs((event.timestamp - incident_time).total_seconds()))
    return min(distances, default=float("inf"))


def build_two_level_evidence_index(
    events: list[LogEvent],
    request: TriageRequest,
    *,
    baseline_indices: list[int] | None = None,
    incident_indices: list[int] | None = None,
    detailed_group_limit: int = DEFAULT_DETAILED_GROUP_LIMIT,
) -> dict[str, Any]:
    """Build an opt-in bounded view while retaining every compact group."""
    if detailed_group_limit < 1:
        raise ValueError("detailed_group_limit must be positive")
    compact = build_compact_evidence(
        events,
        baseline_indices=baseline_indices,
        incident_indices=incident_indices,
    )
    terms = _request_terms(request)
    facts: dict[int, dict[str, Any]] = {}
    for group in compact["groups"]:
        text = " ".join(
            str(group.get(field) or "")
            for field in ("template", "tag", "source", "level")
        ).casefold()
        facts[group["group_id"]] = {
            "overlap": sorted(term for term in terms if term in text),
            "incident": group.get("incident_count", 0) > 0,
            "novel": bool(group.get("not_seen_in_observed_baseline", False)),
            "distance": _group_time_distance(group, events, request.incident_time),
        }

    selected: dict[int, list[str]] = {}
    quota = max(1, detailed_group_limit // 4)

    def add_ranked(groups: list[dict[str, Any]], reason: str) -> None:
        added = 0
        for group in groups:
            group_id = group["group_id"]
            if group_id in selected:
                if reason not in selected[group_id]:
                    selected[group_id].append(reason)
                continue
            if len(selected) >= detailed_group_limit or added >= quota:
                break
            selected[group_id] = [reason]
            added += 1

    groups = compact["groups"]
    overlap = [group for group in groups if facts[group["group_id"]]["overlap"]]
    overlap.sort(
        key=lambda group: (
            -len(facts[group["group_id"]]["overlap"]),
            not facts[group["group_id"]]["incident"],
            facts[group["group_id"]]["distance"],
            group["first_index"],
        )
    )
    add_ranked(overlap, "request_overlap")

    incident = [group for group in groups if facts[group["group_id"]]["incident"]]
    incident.sort(
        key=lambda group: (
            not facts[group["group_id"]]["novel"],
            facts[group["group_id"]]["distance"],
            -group["incident_count"],
            group["first_index"],
        )
    )
    add_ranked(incident, "incident_phase")

    nearby = [
        group
        for group in groups
        if facts[group["group_id"]]["distance"] != float("inf")
    ]
    nearby.sort(
        key=lambda group: (
            facts[group["group_id"]]["distance"],
            -len(facts[group["group_id"]]["overlap"]),
            group["first_index"],
        )
    )
    add_ranked(nearby, "incident_time_proximity")

    diversity = sorted(
        groups,
        key=lambda group: (
            -len(facts[group["group_id"]]["overlap"]),
            not facts[group["group_id"]]["incident"],
            facts[group["group_id"]]["distance"],
            group["first_index"],
        ),
    )
    seen_facets: set[tuple[Any, Any, Any]] = set()
    diverse = []
    for group in diversity:
        facet = (group["source"], group["tag"], group["level"])
        if facet not in seen_facets:
            seen_facets.add(facet)
            diverse.append(group)
    add_ranked(diverse, "source_tag_level_diversity")

    detailed = []
    deferred = []
    for group in groups:
        if group["group_id"] in selected:
            detailed.append({**group, "selection_reasons": selected[group["group_id"]]})
        else:
            deferred.append(group)
    return {
        "event_count": compact["event_count"],
        "group_count": compact["group_count"],
        "detailed_groups": detailed,
        "deferred_groups": deferred,
    }


def two_level_model_view(index: dict[str, Any]) -> dict[str, Any]:
    detailed = [
        {**_model_group(group), "selection_reasons": group["selection_reasons"]}
        for group in index["detailed_groups"]
    ]
    return {
        "event_count": index["event_count"],
        "group_count": index["group_count"],
        "detailed_group_count": len(detailed),
        "deferred_group_count": len(index["deferred_groups"]),
        "detailed_groups": detailed,
        "deferred_index": {
            "query_tool": "query_deferred_groups",
            "filters": ["group_ids", "text", "tag", "source", "phase"],
            "max_results": MAX_GROUP_QUERY_RESULTS,
            "max_bytes": MAX_GROUP_QUERY_BYTES,
        },
    }


def query_deferred_groups(
    index: dict[str, Any],
    *,
    group_ids: list[int] | None = None,
    text: str | None = None,
    tag: str | None = None,
    source: str | None = None,
    phase: str | None = None,
    limit: int = 10,
    max_bytes: int = 8_000,
) -> dict[str, Any]:
    """Query deferred summaries with stable global IDs and hard output bounds."""
    if not 1 <= limit <= MAX_GROUP_QUERY_RESULTS:
        raise ValueError(f"limit must be between 1 and {MAX_GROUP_QUERY_RESULTS}")
    if not 256 <= max_bytes <= MAX_GROUP_QUERY_BYTES:
        raise ValueError(f"max_bytes must be between 256 and {MAX_GROUP_QUERY_BYTES}")
    if phase not in (None, "baseline", "incident", "unassigned"):
        raise ValueError("phase must be baseline, incident, or unassigned")
    if phase is not None and not any(
        "incident_count" in group
        for key in ("detailed_groups", "deferred_groups")
        for group in index[key]
    ):
        raise ValueError("phase query requires explicit baseline/incident indices")
    all_ids = {
        group["group_id"]
        for key in ("detailed_groups", "deferred_groups")
        for group in index[key]
    }
    requested_ids = set(group_ids) if group_ids is not None else None
    if requested_ids is not None and (
        any(type(value) is not int for value in requested_ids)
        or not requested_ids <= all_ids
    ):
        raise ValueError("group_ids must contain known global group IDs")

    matches = []
    needle = text.casefold() if text else None
    for group in index["deferred_groups"]:
        if requested_ids is not None and group["group_id"] not in requested_ids:
            continue
        if tag is not None and (group.get("tag") or "").casefold() != tag.casefold():
            continue
        if source is not None and group["source"].casefold() != source.casefold():
            continue
        if phase is not None and group.get(f"{phase}_count", 0) == 0:
            continue
        haystack = " ".join(
            str(group.get(field) or "")
            for field in ("template", "tag", "source", "level")
        ).casefold()
        if needle is not None and needle not in haystack:
            continue
        matches.append({**_model_group(group), "indices": list(group["indices"])})

    result = {
        "groups": [],
        "count": 0,
        "matched_count": len(matches),
        "truncated": bool(matches),
        "limit": limit,
        "max_bytes": max_bytes,
    }
    for group in matches[:limit]:
        candidate = {**result, "groups": [*result["groups"], group]}
        candidate["count"] = len(candidate["groups"])
        candidate["truncated"] = candidate["count"] < len(matches)
        size = len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode())
        if size > max_bytes:
            break
        result = candidate
    return result


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
