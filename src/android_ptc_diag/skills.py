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
