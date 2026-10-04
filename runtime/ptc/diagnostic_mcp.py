"""Task-specific MCP wrapper for the frozen normalized event snapshot."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

for _path in ("/home/daytona/tools", os.environ.get("ANDROID_PTC_SRC")):
    if _path and Path(_path).exists() and _path not in sys.path:
        sys.path.insert(0, _path)

from android_ptc_diag.models import LogEvent  # noqa: E402
from android_ptc_diag.primitives import (  # noqa: E402
    filter_by_level as _filter_by_level,
    filter_by_tag as _filter_by_tag,
    filter_by_text as _filter_by_text,
    filter_by_time as _filter_by_time,
    first_occurrence as _first_occurrence,
    get_events as _get_events,
    group_by_pid as _group_by_pid,
    group_by_template as _group_by_template,
)
from mcp.server.fastmcp import FastMCP  # noqa: E402


def _load_events() -> list[LogEvent]:
    encoded = os.environ.get("PTC_SNAPSHOT_B64")
    if not encoded:
        raise RuntimeError("PTC_SNAPSHOT_B64 is required")
    snapshot = json.loads(base64.b64decode(encoded).decode("utf-8"))
    return [LogEvent.model_validate(item) for item in snapshot["events"]]


events = _load_events()
mcp = FastMCP("android-diagnostic")
TRACE_FILE = os.environ.get("PTC_PRIMITIVE_TRACE_FILE")
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]+")


def _write_trace(record: dict[str, Any]) -> None:
    if not TRACE_FILE:
        return
    try:
        path = Path(TRACE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        # Tracing must never change the primitive's normal success/error semantics.
        return


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    def redact(value: Any) -> Any:
        if isinstance(value, str):
            return SECRET_PATTERN.sub("<redacted>", value)
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        return value

    return redact(arguments)


def _invoke_traced(name: str, function: Any, **arguments: Any) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = function(events, **arguments)
    except Exception as exc:  # noqa: BLE001 - preserve the original primitive error
        _write_trace(
            {
                "primitive": name,
                "arguments": _safe_arguments(arguments),
                "record_count": None,
                "result_bytes": 0,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "error": str(exc),
            }
        )
        raise
    serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    record_count = result.get("count") if isinstance(result.get("count"), int) else None
    _write_trace(
        {
            "primitive": name,
            "arguments": _safe_arguments(arguments),
            "record_count": record_count,
            "result_bytes": len(serialized.encode("utf-8")),
            "latency_ms": (time.perf_counter() - started) * 1000,
            "error": None,
        }
    )
    return result


@mcp.tool()
def get_events(indices: list[int] | None = None) -> dict[str, Any]:
    """Read full normalized event records for explicit indices."""
    return _invoke_traced("get_events", _get_events, indices=indices)


@mcp.tool()
def filter_by_time(
    indices: list[int] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Return event indices in an ISO wall-clock time range."""
    return _invoke_traced("filter_by_time", _filter_by_time, indices=indices, start=start, end=end)


@mcp.tool()
def filter_by_level(
    indices: list[int] | None = None,
    levels: list[str] | None = None,
) -> dict[str, Any]:
    """Return event indices with one of the requested log levels."""
    return _invoke_traced("filter_by_level", _filter_by_level, indices=indices, levels=levels)


@mcp.tool()
def filter_by_text(
    indices: list[int] | None = None,
    text: str = "",
) -> dict[str, Any]:
    """Return indices whose message, tag, or component contains text."""
    return _invoke_traced("filter_by_text", _filter_by_text, indices=indices, text=text)


@mcp.tool()
def filter_by_tag(
    indices: list[int] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Return indices whose tag or component exactly matches a requested value."""
    return _invoke_traced("filter_by_tag", _filter_by_tag, indices=indices, tags=tags)


@mcp.tool()
def group_by_template(indices: list[int] | None = None) -> dict[str, Any]:
    """Group selected events using existing Drain3 template metadata."""
    return _invoke_traced("group_by_template", _group_by_template, indices=indices)


@mcp.tool()
def first_occurrence(indices: list[int] | None = None) -> dict[str, Any]:
    """Return the first original index for each selected template group."""
    return _invoke_traced("first_occurrence", _first_occurrence, indices=indices)


@mcp.tool()
def group_by_pid(indices: list[int] | None = None) -> dict[str, Any]:
    """Group selected Logcat events by normalized PID."""
    return _invoke_traced("group_by_pid", _group_by_pid, indices=indices)


if __name__ == "__main__":
    mcp.run(transport="stdio")
