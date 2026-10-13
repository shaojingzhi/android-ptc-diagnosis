"""Small deterministic evidence-manipulation primitives for M3 execution modes."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Callable

from .clustering import group_events_by_identity
from .models import LogEvent


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
    }
