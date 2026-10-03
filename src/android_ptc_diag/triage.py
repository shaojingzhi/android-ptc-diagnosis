from __future__ import annotations

from .models import LogEvent, TriageRequest, TriageResult


def fixed_triage(events: list[LogEvent], request: TriageRequest) -> TriageResult:
    """M2 target.

    Keep this deliberately boring: deterministic preprocessing and ranking only.
    PTC will later be compared against the same task, not hidden inside this file.
    """
    raise NotImplementedError("M2: normalize/cluster/rank baseline")
