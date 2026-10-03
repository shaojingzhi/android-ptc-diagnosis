from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LogEvent(BaseModel):
    source: Literal["logcat", "kmsg", "replay"]
    timestamp: datetime | None = None
    level: str | None = None
    pid: int | None = None
    tid: int | None = None
    tag: str | None = None
    component: str | None = None
    message: str
    template_id: str | None = None
    repeat_count: int = 1


class TriageRequest(BaseModel):
    symptom: str
    target_package: str | None = None
    failure_timestamp: datetime | None = None


class FailureCandidate(BaseModel):
    event_index: int
    score: float = Field(ge=0.0, le=1.0)
    reason: list[str] = Field(default_factory=list)


class TriageResult(BaseModel):
    primary_candidates: list[FailureCandidate] = Field(default_factory=list)
    propagation_event_indices: list[int] = Field(default_factory=list)
    evidence_event_indices: list[int] = Field(default_factory=list)
    noise_event_indices: list[int] = Field(default_factory=list)
