from __future__ import annotations

import math

from .clustering import cluster_events
from .models import FailureCandidate, LogEvent, TriageRequest, TriageResult


_SEVERITY_SCORE = {
    "F": 0.40,
    "E": 0.40,
    "W": 0.20,
    "I": 0.10,
    "D": 0.05,
    "V": 0.02,
}


def _score_event(
    event: LogEvent,
    request: TriageRequest,
    first_index: int,
    event_count: int,
) -> tuple[float, list[str]]:
    reasons: list[str] = []

    severity = _SEVERITY_SCORE.get(event.level or "", 0.05)
    reasons.append(f"severity {event.level or 'unknown'}: +{severity:.3f}")

    temporal = 0.0
    if event.timestamp is not None and request.failure_timestamp is not None:
        distance = abs((request.failure_timestamp - event.timestamp).total_seconds())
        temporal = 0.30 * max(0.0, 1.0 - distance / 30.0)
        reasons.append(f"failure time distance {distance:.3f}s: +{temporal:.3f}")

    first_seen = 0.05 * (1.0 - first_index / max(event_count - 1, 1))
    reasons.append(f"first seen at event {first_index}: +{first_seen:.3f}")

    target = 0.0
    if request.target_package:
        searchable = " ".join(filter(None, (event.tag, event.component, event.message))).lower()
        if request.target_package.lower() in searchable:
            target = 0.15
            reasons.append(f"mentions target package: +{target:.3f}")

    source = 0.05 if event.timestamp is not None else 0.0
    if source:
        reasons.append(f"wall-clock timestamp available: +{source:.3f}")

    repetition = 0.04 * math.log2(event.repeat_count) if event.repeat_count > 1 else 0.0
    if repetition:
        reasons.append(f"repeated {event.repeat_count} times: -{repetition:.3f}")

    score = max(0.0, min(1.0, severity + temporal + first_seen + target + source - repetition))
    return score, reasons


def fixed_triage(events: list[LogEvent], request: TriageRequest) -> TriageResult:
    """Cluster duplicates and rank first occurrences with fixed signals only."""
    representative_indices = cluster_events(events)
    candidates = []
    for index in representative_indices:
        score, reasons = _score_event(events[index], request, index, len(events))
        candidates.append(FailureCandidate(event_index=index, score=score, reason=reasons))

    candidates.sort(key=lambda candidate: (-candidate.score, candidate.event_index))
    representative_set = set(representative_indices)
    duplicate_indices = [index for index in range(len(events)) if index not in representative_set]
    return TriageResult(
        primary_candidates=candidates,
        evidence_event_indices=representative_indices,
        noise_event_indices=duplicate_indices,
    )
