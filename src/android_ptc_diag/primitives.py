"""Small deterministic evidence-manipulation primitives for M3 execution modes."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
from typing import Any, Callable

from .clustering import group_events_by_identity
from .models import EntityReference, LogEvent
from .skills import build_evidence_view, select_group_evidence_indices


def _indices(events: list[LogEvent], selected: list[int] | None) -> list[int]:
    values = list(range(len(events))) if selected is None else selected
    result: list[int] = []
    for index in values:
        if not isinstance(index, int) or index < 0 or index >= len(events):
            raise ValueError(f"event index out of range: {index}")
        if index not in result:
            result.append(index)
    return result


def _index_result(indices: list[int]) -> dict[str, Any]:
    return {"indices": indices, "count": len(indices)}


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _event_dict(index: int, event: LogEvent) -> dict[str, Any]:
    return {
        "index": index,
        "source": event.source,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "monotonic_seconds": event.monotonic_seconds,
        "level": event.level,
        "pid": event.pid,
        "tid": event.tid,
        "tag": event.tag,
        "component": event.component,
        "message": event.message,
        "template_id": event.template_id,
        "template": event.template,
        "repeat_count": event.repeat_count,
    }


def get_events(events: list[LogEvent], indices: list[int] | None = None) -> dict[str, Any]:
    selected = _indices(events, indices)
    values = [_event_dict(index, events[index]) for index in selected]
    return {"events": values, "count": len(values)}


def filter_by_time(
    events: list[LogEvent],
    indices: list[int] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    start_time = _parse_time(start)
    end_time = _parse_time(end)
    selected = []
    for index in _indices(events, indices):
        timestamp = events[index].timestamp
        if timestamp is None:
            continue
        if start_time is not None and timestamp < start_time:
            continue
        if end_time is not None and timestamp > end_time:
            continue
        selected.append(index)
    return _index_result(selected)


def filter_by_level(
    events: list[LogEvent],
    indices: list[int] | None = None,
    levels: list[str] | None = None,
) -> dict[str, Any]:
    allowed = {level.upper() for level in (levels or [])}
    selected = [
        index for index in _indices(events, indices) if (events[index].level or "").upper() in allowed
    ]
    return _index_result(selected)


def filter_by_text(
    events: list[LogEvent],
    indices: list[int] | None = None,
    text: str = "",
) -> dict[str, Any]:
    needle = text.casefold()
    selected = []
    for index in _indices(events, indices):
        event = events[index]
        haystack = " ".join(filter(None, (event.message, event.tag, event.component))).casefold()
        if needle and needle in haystack:
            selected.append(index)
    return _index_result(selected)


def filter_by_tag(
    events: list[LogEvent],
    indices: list[int] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    allowed = {tag.casefold() for tag in (tags or [])}
    selected = [
        index
        for index in _indices(events, indices)
        if (events[index].tag or "").casefold() in allowed
        or (events[index].component or "").casefold() in allowed
    ]
    return _index_result(selected)


def group_by_template(
    events: list[LogEvent],
    indices: list[int] | None = None,
) -> dict[str, Any]:
    groups = group_events_by_identity(events, _indices(events, indices))
    return {"groups": groups, "count": len(groups)}


def first_occurrence(
    events: list[LogEvent],
    indices: list[int] | None = None,
) -> dict[str, Any]:
    selected = _indices(events, indices)
    groups = group_by_template(events, selected)["groups"]
    positions = {index: position for position, index in enumerate(selected)}
    groups.sort(key=lambda group: min(positions[index] for index in group["indices"]))
    return _index_result([group["first_index"] for group in groups])


def group_by_pid(
    events: list[LogEvent],
    indices: list[int] | None = None,
) -> dict[str, Any]:
    groups: dict[str, list[int]] = {}
    for index in _indices(events, indices):
        pid = events[index].pid
        if pid is not None:
            groups.setdefault(str(pid), []).append(index)
    values = [{"pid": int(pid), "indices": values, "count": len(values)} for pid, values in groups.items()]
    return {"groups": values, "count": len(values)}


def _context_clock(event: LogEvent) -> tuple[str, datetime | float] | None:
    if event.source == "kmsg":
        return (
            ("kmessage_monotonic", event.monotonic_seconds)
            if event.monotonic_seconds is not None
            else None
        )
    if event.timestamp is None:
        return None
    awareness = "aware" if event.timestamp.utcoffset() is not None else "naive"
    return (f"wall_clock_{awareness}", event.timestamp)


def _context_affinity(
    event: LogEvent, candidate: LogEvent, same_group: bool
) -> tuple[tuple[bool | int, ...], str]:
    same_pid = event.pid is not None and event.pid == candidate.pid
    same_source = event.source == candidate.source
    same_component = same_source and (
        (event.tag is not None and event.tag == candidate.tag)
        or (event.component is not None and event.component == candidate.component)
    )
    source_affinity = 2 if same_component else 1 if same_source else 0
    relation = (
        "same_pid"
        if same_pid
        else "same_source_tag_component"
        if source_affinity
        else "same_group"
        if same_group
        else "temporal_proximity"
    )
    return (not same_pid, -source_affinity, not same_group), relation


def inspect_failure_context(
    events: list[LogEvent],
    candidate_indices: list[int],
    *,
    before_seconds: float = 5.0,
    after_seconds: float = 5.0,
    max_events: int = 50,
) -> dict[str, Any]:
    """Return bounded source-local evidence around existing failure candidates."""
    if not isinstance(candidate_indices, list) or not candidate_indices:
        raise ValueError("candidate_indices must be a non-empty list")
    if any(
        not isinstance(index, int) or isinstance(index, bool)
        for index in candidate_indices
    ):
        raise ValueError("candidate_indices must contain integers")
    candidates = _indices(events, candidate_indices)
    for name, value in (("before_seconds", before_seconds), ("after_seconds", after_seconds)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number")
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and non-negative")
    if not isinstance(max_events, int) or isinstance(max_events, bool) or max_events < 1:
        raise ValueError("max_events must be a positive integer")
    if len(candidates) > max_events:
        raise ValueError("max_events must be at least the number of candidates")

    group_ids = {
        index: group["group_id"]
        for group in group_events_by_identity(events)
        for index in group["indices"]
    }
    candidate_set = set(candidates)
    candidate_clocks = {index: _context_clock(events[index]) for index in candidates}
    ranked: list[tuple[tuple[Any, ...], int, str]] = []
    for index, event in enumerate(events):
        if index in candidate_set or (clock := _context_clock(event)) is None:
            continue
        comparisons = []
        for candidate_index, candidate_clock in candidate_clocks.items():
            if candidate_clock is None or clock[0] != candidate_clock[0]:
                continue
            delta = clock[1] - candidate_clock[1]
            seconds = delta.total_seconds() if isinstance(delta, timedelta) else delta
            if -before_seconds <= seconds <= after_seconds:
                affinity, relation = _context_affinity(
                    event,
                    events[candidate_index],
                    group_ids[index] == group_ids[candidate_index],
                )
                comparisons.append(((*affinity, abs(seconds), index), relation))
        if comparisons:
            rank, relation = min(comparisons)
            ranked.append((rank, index, relation))

    ranked.sort(key=lambda item: item[0])
    selected_context = ranked[: max_events - len(candidates)]
    truncated = len(ranked) > len(selected_context)
    selected = [(index, "candidate") for index in candidates]
    selected.extend((index, relation) for _, index, relation in selected_context)
    records = [
        {
            **_event_dict(index, events[index]),
            "raw_line_number": events[index].raw_line_number,
            "group_id": group_ids[index],
            "relation": relation,
        }
        for index, relation in selected
    ]

    clocks = [_context_clock(events[record["index"]]) for record in records]
    domains = {clock[0] for clock in clocks if clock is not None}
    values = [clock[1] for clock in clocks if clock is not None]
    time_range: dict[str, Any] = {"start": None, "end": None}
    if len(domains) == 1 and values:
        start, end = min(values), max(values)
        time_range = {
            "start": start.isoformat() if isinstance(start, datetime) else start,
            "end": end.isoformat() if isinstance(end, datetime) else end,
        }

    limitations = []
    if truncated:
        limitations.append("context truncated by max_events")
    if len(domains) > 1:
        limitations.append("cross-source time order is unknown")
    missing_clocks = [index for index, clock in candidate_clocks.items() if clock is None]
    if missing_clocks:
        limitations.append(f"candidate indices without comparable time: {missing_clocks}")

    return {
        "candidate_indices": candidates,
        "context_indices": [index for _, index, _ in selected_context],
        "events": records,
        "evidence_summary": {
            "event_count": len(records),
            "sources": sorted({record["source"] for record in records}),
            "pids": sorted({record["pid"] for record in records if record["pid"] is not None}),
            "groups": sorted({record["group_id"] for record in records}),
            "time_range": time_range,
        },
        "cross_source_order": "unknown" if len(domains) > 1 else "single_time_domain",
        "truncated": truncated,
        "limitations": limitations,
        "read_only": True,
    }


def _inspect_runtime_entry(
    runtime_state: dict[str, Any],
    collection: str,
    key: str,
    label: str,
) -> dict[str, Any]:
    entries = runtime_state.get(collection)
    if not isinstance(entries, dict) or key not in entries:
        raise ValueError(f"unknown {label}: {key}")
    value = entries[key]
    if not isinstance(value, dict):
        raise ValueError(f"invalid {label} state: {key}")
    return {label: key, **value, "read_only": True}


def discover_runtime_entities(
    runtime_state: dict[str, Any], *, entity_type: str
) -> dict[str, Any]:
    """List canonical read-only runtime-state targets without interpreting them."""
    collections = {
        "process": ("processes", "name"),
        "service": ("services", "name"),
        "mount": ("mounts", "path"),
    }
    if entity_type not in collections:
        raise ValueError(f"unsupported runtime entity type: {entity_type}")
    collection, identifier_field = collections[entity_type]
    entries = runtime_state.get(collection, {})
    if not isinstance(entries, dict):
        raise ValueError(f"invalid {entity_type} state collection")

    entities = []
    for identifier in sorted(entries):
        attributes = entries[identifier]
        if not isinstance(attributes, dict):
            raise ValueError(f"invalid {entity_type} state: {identifier}")
        entity_ref = EntityReference(
            entity_type=entity_type,
            entity_id=f"{entity_type}:{identifier}",
            canonical_name=identifier,
        )
        entities.append(
            {
                identifier_field: identifier,
                "entity_ref": entity_ref.model_dump(),
                "attributes": dict(attributes),
            }
        )
    return {
        "entity_type": entity_type,
        "entities": entities,
        "count": len(entities),
        "read_only": True,
    }


def _resolve_entity_reference(
    runtime_state: dict[str, Any],
    entity_ref: EntityReference | dict[str, Any],
    expected_type: str,
) -> str:
    try:
        reference = EntityReference.model_validate(entity_ref)
    except Exception as exc:
        raise ValueError(f"invalid entity reference: {exc}") from None
    if reference.entity_type != expected_type:
        raise ValueError(
            f"entity reference type mismatch: expected {expected_type}, "
            f"got {reference.entity_type}"
        )

    collection = {
        "process": "processes",
        "service": "services",
        "mount": "mounts",
    }[expected_type]
    entries = runtime_state.get(collection)
    if not isinstance(entries, dict):
        raise ValueError(f"invalid {expected_type} state collection")
    matches = [
        identifier
        for identifier in entries
        if reference.entity_id == f"{expected_type}:{identifier}"
    ]
    if len(matches) != 1:
        raise ValueError(f"unknown {expected_type} entity reference: {reference.entity_id}")

    identifier = matches[0]
    if reference.canonical_name is not None and reference.canonical_name != identifier:
        raise ValueError(
            f"entity reference canonical name mismatch: expected {identifier}, "
            f"got {reference.canonical_name}"
        )
    return identifier


def inspect_process_state_by_ref(
    runtime_state: dict[str, Any], *, entity_ref: EntityReference | dict[str, Any]
) -> dict[str, Any]:
    """Read one replay process selected by a validated canonical reference."""
    name = _resolve_entity_reference(runtime_state, entity_ref, "process")
    return inspect_process_state(runtime_state, name=name)


def inspect_service_state_by_ref(
    runtime_state: dict[str, Any], *, entity_ref: EntityReference | dict[str, Any]
) -> dict[str, Any]:
    """Read one replay service selected by a validated canonical reference."""
    name = _resolve_entity_reference(runtime_state, entity_ref, "service")
    return inspect_service_state(runtime_state, name=name)


def inspect_mount_state_by_ref(
    runtime_state: dict[str, Any], *, entity_ref: EntityReference | dict[str, Any]
) -> dict[str, Any]:
    """Read one replay mount selected by a validated canonical reference."""
    path = _resolve_entity_reference(runtime_state, entity_ref, "mount")
    return inspect_mount_state(runtime_state, path=path)


def inspect_process_state(
    runtime_state: dict[str, Any], *, name: str | None = None, pid: int | None = None
) -> dict[str, Any]:
    """Read one process by name, pid, or a consistent pair of locators."""
    if name is None and pid is None:
        raise ValueError("process locator requires name or pid")
    if name is not None and not isinstance(name, str):
        raise ValueError("process name must be a string")
    if pid is not None and (not isinstance(pid, int) or isinstance(pid, bool)):
        raise ValueError("process pid must be an integer")

    entries = runtime_state.get("processes")
    if not isinstance(entries, dict):
        raise ValueError("invalid process state collection")
    if pid is None:
        if name not in entries:
            raise ValueError(f"unknown process name: {name}")
        value = entries[name]
        if not isinstance(value, dict):
            raise ValueError(f"invalid process state: {name}")
        return {"process": name, **value, "read_only": True}

    matches = [
        (entry_name, value)
        for entry_name, value in entries.items()
        if isinstance(value, dict) and value.get("pid") == pid
    ]
    if len(matches) == 0:
        raise ValueError(f"unknown process pid: {pid}")
    if len(matches) > 1:
        raise ValueError(f"non-unique process pid: {pid}")

    resolved_name = matches[0][0]
    value = entries[resolved_name]
    if name is not None and name in entries and name != resolved_name:
        named_value = entries[name]
        named_pid = named_value.get("pid") if isinstance(named_value, dict) else None
        raise ValueError(
            f"process locator conflict: {name} has pid {named_pid}, not {pid}"
        )
    return {"process": resolved_name, **value, "read_only": True}


def inspect_cgroup_state(runtime_state: dict[str, Any], *, name: str) -> dict[str, Any]:
    """Read one deterministic replay cgroup record without changing state."""
    return _inspect_runtime_entry(runtime_state, "cgroups", name, "cgroup")


def inspect_service_state(runtime_state: dict[str, Any], *, name: str) -> dict[str, Any]:
    """Read one deterministic replay service record without changing state."""
    return _inspect_runtime_entry(runtime_state, "services", name, "service")


def inspect_mount_state(runtime_state: dict[str, Any], *, path: str) -> dict[str, Any]:
    """Read one deterministic replay mount record without changing state."""
    return _inspect_runtime_entry(runtime_state, "mounts", path, "mount")


def _function_spec(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    parameters: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


_INDICES = {"type": "array", "items": {"type": "integer"}}
_TOOL_DEFINITIONS = [
    _function_spec(
        "get_events",
        "Read full normalized event records for explicit indices.",
        {"indices": _INDICES},
    ),
    _function_spec(
        "filter_by_time",
        "Return event indices in an ISO wall-clock time range.",
        {"indices": _INDICES, "start": {"type": "string"}, "end": {"type": "string"}},
    ),
    _function_spec(
        "filter_by_level",
        "Return event indices with one of the requested log levels.",
        {"indices": _INDICES, "levels": {"type": "array", "items": {"type": "string"}}},
    ),
    _function_spec(
        "filter_by_text",
        "Return indices whose message, tag, or component contains text.",
        {"indices": _INDICES, "text": {"type": "string"}},
        ["text"],
    ),
    _function_spec(
        "filter_by_tag",
        "Return indices whose tag or component exactly matches a requested value.",
        {"indices": _INDICES, "tags": {"type": "array", "items": {"type": "string"}}},
    ),
    _function_spec(
        "group_by_template",
        "Group selected events using existing Drain3 template metadata.",
        {"indices": _INDICES},
    ),
    _function_spec(
        "first_occurrence",
        "Return the first original index for each selected template group.",
        {"indices": _INDICES},
    ),
    _function_spec(
        "group_by_pid",
        "Group selected Logcat events by normalized PID.",
        {"indices": _INDICES},
    ),
    _function_spec(
        "inspect_failure_context",
        "Read bounded contextual evidence around existing failure-candidate indices; "
        "this does not infer root cause.",
        {
            "candidate_indices": _INDICES,
            "before_seconds": {"type": "number", "minimum": 0},
            "after_seconds": {"type": "number", "minimum": 0},
            "max_events": {"type": "integer", "minimum": 1},
        },
        ["candidate_indices"],
    ),
]


def tool_definitions() -> list[dict[str, Any]]:
    return json.loads(json.dumps(_TOOL_DEFINITIONS))


def tool_handlers(events: list[LogEvent]) -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "get_events": lambda **kwargs: get_events(events, **kwargs),
        "filter_by_time": lambda **kwargs: filter_by_time(events, **kwargs),
        "filter_by_level": lambda **kwargs: filter_by_level(events, **kwargs),
        "filter_by_text": lambda **kwargs: filter_by_text(events, **kwargs),
        "filter_by_tag": lambda **kwargs: filter_by_tag(events, **kwargs),
        "group_by_template": lambda **kwargs: group_by_template(events, **kwargs),
        "first_occurrence": lambda **kwargs: first_occurrence(events, **kwargs),
        "group_by_pid": lambda **kwargs: group_by_pid(events, **kwargs),
        "inspect_failure_context": lambda **kwargs: inspect_failure_context(
            events, **kwargs
        ),
    }


def typed_reference_tool_definitions() -> list[dict[str, Any]]:
    """Nine-tool evaluation surface with inventory-backed runtime references."""
    entity_ref = {
        "type": "object",
        "properties": {
            "entity_type": {
                "type": "string",
                "enum": ["process", "service", "mount"],
            },
            "entity_id": {"type": "string"},
            "canonical_name": {"type": ["string", "null"]},
        },
        "required": ["entity_type", "entity_id"],
        "additionalProperties": False,
    }
    return [
        _function_spec(
            "get_events",
            "Read full normalized event records for explicit indices.",
            {"indices": _INDICES},
        ),
        _function_spec(
            "filter_by_text",
            "Return indices whose message, tag, or component contains text.",
            {"indices": _INDICES, "text": {"type": "string"}},
            ["text"],
        ),
        _function_spec(
            "inspect_group_evidence",
            "Read bounded source-local evidence for compact group IDs.",
            {
                "group_ids": _INDICES,
                "event_count": {"type": "integer", "minimum": 0},
                "boundary_radius": {"type": "integer", "minimum": 0},
            },
            ["group_ids", "event_count"],
        ),
        _function_spec(
            "inspect_failure_context",
            "Read bounded context around failure-candidate event indices without inferring cause.",
            {
                "candidate_indices": _INDICES,
                "before_seconds": {"type": "number", "minimum": 0},
                "after_seconds": {"type": "number", "minimum": 0},
                "max_events": {"type": "integer", "minimum": 1},
            },
            ["candidate_indices"],
        ),
        _function_spec(
            "discover_runtime_entities",
            "List canonical read-only references for one runtime entity type.",
            {
                "entity_type": {
                    "type": "string",
                    "enum": ["process", "service", "mount"],
                }
            },
            ["entity_type"],
        ),
        _function_spec(
            "inspect_process_state_by_ref",
            "Read process state using a reference returned by discovery.",
            {"entity_ref": entity_ref},
            ["entity_ref"],
        ),
        _function_spec(
            "inspect_service_state_by_ref",
            "Read service state using a reference returned by discovery.",
            {"entity_ref": entity_ref},
            ["entity_ref"],
        ),
        _function_spec(
            "inspect_cgroup_state",
            "Read one deterministic replay cgroup record without changing state.",
            {"name": {"type": "string"}},
            ["name"],
        ),
        _function_spec(
            "inspect_mount_state_by_ref",
            "Read mount state using a reference returned by discovery.",
            {"entity_ref": entity_ref},
            ["entity_ref"],
        ),
    ]


def typed_reference_tool_handlers(
    events: list[LogEvent], runtime_state: dict[str, Any]
) -> dict[str, Callable[..., dict[str, Any]]]:
    """Bind the typed-reference tool surface to one replay run."""

    def inspect_groups(
        group_ids: list[int], event_count: int, boundary_radius: int = 1
    ) -> dict[str, Any]:
        if event_count != len(events):
            raise ValueError(
                f"event_count must equal replay event count {len(events)}"
            )
        available = {
            group["group_id"]: group for group in group_by_template(events)["groups"]
        }
        selected = []
        for group_id in group_ids:
            if isinstance(group_id, bool) or not isinstance(group_id, int):
                raise ValueError(f"invalid group_id: {group_id}")
            group = available.get(group_id)
            if group is None:
                raise ValueError(f"invalid group_id: {group_id}")
            selected.append(
                {
                    "representative_index": group["first_index"],
                    "first_index": group["first_index"],
                    "last_index": group["last_index"],
                }
            )
        indices = select_group_evidence_indices(
            selected, event_count, boundary_radius=boundary_radius
        )
        records = get_events(events, indices=indices)["events"]
        return {
            "indices": indices,
            "count": len(records),
            "evidence_view": build_evidence_view(records),
        }

    return {
        "get_events": lambda **kwargs: get_events(events, **kwargs),
        "filter_by_text": lambda **kwargs: filter_by_text(events, **kwargs),
        "inspect_group_evidence": inspect_groups,
        "inspect_failure_context": lambda **kwargs: inspect_failure_context(
            events, **kwargs
        ),
        "discover_runtime_entities": lambda **kwargs: discover_runtime_entities(
            runtime_state, **kwargs
        ),
        "inspect_process_state_by_ref": lambda **kwargs: inspect_process_state_by_ref(
            runtime_state, **kwargs
        ),
        "inspect_service_state_by_ref": lambda **kwargs: inspect_service_state_by_ref(
            runtime_state, **kwargs
        ),
        "inspect_cgroup_state": lambda **kwargs: inspect_cgroup_state(
            runtime_state, **kwargs
        ),
        "inspect_mount_state_by_ref": lambda **kwargs: inspect_mount_state_by_ref(
            runtime_state, **kwargs
        ),
    }
