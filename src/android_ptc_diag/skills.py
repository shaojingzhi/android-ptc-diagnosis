"""Reusable deterministic mechanics for inspecting compact evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def select_group_evidence_indices(
    groups: list[dict[str, Any]],
    event_count: int,
    *,
    boundary_radius: int = 1,
) -> list[int]:
    """Choose evidence locations; primitives control access to raw event data."""
    if event_count < 0:
        raise ValueError("event_count must be non-negative")
    if boundary_radius < 0:
        raise ValueError("boundary_radius must be non-negative")

    selected: set[int] = set()
    for group in groups:
        values = {
            field: group[field]
            for field in ("representative_index", "first_index", "last_index")
        }
        for field, index in values.items():
            if not isinstance(index, int) or not 0 <= index < event_count:
                raise ValueError(f"invalid {field}: {index}")
            selected.add(index)
        for boundary in (values["first_index"], values["last_index"]):
            start = max(0, boundary - boundary_radius)
            end = min(event_count, boundary + boundary_radius + 1)
            selected.update(range(start, end))

    return sorted(selected)


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def build_timeline(selected_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate selected evidence and arrange it into a deterministic timeline."""
    by_index: dict[int, dict[str, Any]] = {}
    for event in selected_events:
        index = event.get("index")
        if not isinstance(index, int):
            raise ValueError(f"invalid event index: {index}")
        by_index.setdefault(index, event)

    entries = [
        {
            "index": index,
            "timestamp": event.get("timestamp"),
            "source": event.get("source"),
            "level": event.get("level"),
            "tag": event.get("tag"),
            "pid": event.get("pid"),
            "message": event.get("message"),
            "template_id": event.get("template_id"),
        }
        for index, event in by_index.items()
    ]
    parsed = [_parse_timestamp(entry["timestamp"]) for entry in entries]
    comparable = bool(entries) and all(timestamp is not None for timestamp in parsed)
    if comparable:
        awareness = {timestamp.tzinfo is not None for timestamp in parsed if timestamp is not None}
        comparable = len(awareness) == 1
    if comparable:
        timestamps = {entry["index"]: timestamp for entry, timestamp in zip(entries, parsed)}
        entries.sort(key=lambda entry: (timestamps[entry["index"]], entry["index"]))
    else:
        entries.sort(key=lambda entry: entry["index"])
    return entries


def build_evidence_view(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep mixed-source evidence source-local when clocks are incomparable.

    Logcat wall-clock timestamps and KMessage monotonic seconds have no shared
    epoch in the normalized data.  This view therefore exposes independently
    ordered source buckets and intentionally does not return a global event
    sequence.
    """
    sources: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        index = event.get("index")
        source = event.get("source")
        if not isinstance(index, int) or not isinstance(source, str):
            raise ValueError("evidence events require integer index and source")
        bucket = "kmessage" if source == "kmsg" else source
        sources.setdefault(bucket, []).append(dict(event))

    for bucket, values in sources.items():
        clocks: list[Any] = []
        for value in values:
            if bucket == "kmessage":
                clock = value.get("monotonic_seconds")
            else:
                clock = _parse_timestamp(value.get("timestamp"))
            clocks.append(clock)

        try:
            values[:] = [
                value
                for _, value in sorted(
                    zip(clocks, values),
                    key=lambda item: (
                        item[0] is None,
                        item[0] if item[0] is not None else 0,
                        item[1]["index"],
                    ),
                )
            ]
        except TypeError:
            # Mixed timestamp awareness/representations are not comparable;
            # use a deterministic per-source fallback, never a cross-source one.
            values.sort(key=lambda value: value["index"])

    return {
        "ordering": "source_local",
        "cross_source_order": "unknown",
        "sources": sources,
    }


def inspect_group_evidence(
    groups: list[dict[str, Any]],
    event_count: int,
    *,
    boundary_radius: int = 1,
) -> dict[str, Any]:
    """Select, fetch, and timeline bounded raw evidence for compact groups.

    The sandbox runtime provides ``tools.diagnostic.get_events``. Keeping that
    import inside the skill makes the raw-evidence boundary explicit while
    allowing the lower-level selection and timeline helpers to stay reusable.
    """
    from tools.diagnostic import get_events

    indices = select_group_evidence_indices(
        groups, event_count, boundary_radius=boundary_radius
    )
    fetched = get_events(indices=indices)
    events = fetched.get("events", [])
    return {
        "indices": indices,
        "evidence_view": build_evidence_view(events),
        "count": len(events),
    }
