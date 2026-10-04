"""Task-specific MCP wrapper for the frozen normalized event snapshot."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys
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


@mcp.tool()
def get_events(indices: list[int] | None = None) -> dict[str, Any]:
    """Read full normalized event records for explicit indices."""
    return _get_events(events, indices)


@mcp.tool()
def filter_by_time(
    indices: list[int] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Return event indices in an ISO wall-clock time range."""
    return _filter_by_time(events, indices, start, end)


@mcp.tool()
def filter_by_level(
    indices: list[int] | None = None,
    levels: list[str] | None = None,
) -> dict[str, Any]:
    """Return event indices with one of the requested log levels."""
    return _filter_by_level(events, indices, levels)


@mcp.tool()
def filter_by_text(
    indices: list[int] | None = None,
    text: str = "",
) -> dict[str, Any]:
    """Return indices whose message, tag, or component contains text."""
    return _filter_by_text(events, indices, text)


@mcp.tool()
def filter_by_tag(
    indices: list[int] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Return indices whose tag or component exactly matches a requested value."""
    return _filter_by_tag(events, indices, tags)


@mcp.tool()
def group_by_template(indices: list[int] | None = None) -> dict[str, Any]:
    """Group selected events using existing Drain3 template metadata."""
    return _group_by_template(events, indices)


@mcp.tool()
def first_occurrence(indices: list[int] | None = None) -> dict[str, Any]:
    """Return the first original index for each selected template group."""
    return _first_occurrence(events, indices)


@mcp.tool()
def group_by_pid(indices: list[int] | None = None) -> dict[str, Any]:
    """Group selected Logcat events by normalized PID."""
    return _group_by_pid(events, indices)


if __name__ == "__main__":
    mcp.run(transport="stdio")
