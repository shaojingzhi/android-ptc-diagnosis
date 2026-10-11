from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class LogEvent(BaseModel):
    source: Literal["logcat", "kmsg", "replay"]
    timestamp: datetime | None = None
    monotonic_seconds: float | None = None
    level: str | None = None
    pid: int | None = None
    tid: int | None = None
    tag: str | None = None
    component: str | None = None
    message: str
    template_id: str | None = None
    template: str | None = None
    repeat_count: int = 1


class TriageRequest(BaseModel):
    symptom: str
    target_package: str | None = None
    failure_timestamp: datetime | None = None
    diagnosis_scope: str | None = None


class FailureCandidate(BaseModel):
    event_index: int
    score: float = Field(ge=0.0, le=1.0)
    reason: list[str] = Field(default_factory=list)


class TriageResult(BaseModel):
    primary_candidates: list[FailureCandidate] = Field(default_factory=list)
    propagation_event_indices: list[int] = Field(default_factory=list)
    evidence_event_indices: list[int] = Field(default_factory=list)
    suppressed_duplicate_indices: list[int] = Field(default_factory=list)
    noise_event_indices: list[int] = Field(default_factory=list)


class DirectDiagnosis(BaseModel):
    primary_candidate_indices: list[int] = Field(default_factory=list)
    evidence_indices: list[int] = Field(default_factory=list)
    propagation_event_indices: list[int] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    recommended_next_checks: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FollowupRequest(BaseModel):
    uncertainty: str
    check: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str


class DiagnosisLoopDecision(BaseModel):
    diagnosis: DirectDiagnosis
    followup_request: FollowupRequest | None = None


class DiagnosisLoopStep(BaseModel):
    current_diagnosis: DirectDiagnosis
    uncertainties: list[str] = Field(default_factory=list)
    chosen_check: str
    why_this_check: str
    check_arguments: dict[str, Any] = Field(default_factory=dict)
    returned_evidence: dict[str, Any] = Field(default_factory=dict)
    updated_diagnosis: DirectDiagnosis
    stop_reason: str | None = None


class DiagnosisLoopState(BaseModel):
    current_diagnosis: DirectDiagnosis
    uncertainties: list[str] = Field(default_factory=list)
    steps: list[DiagnosisLoopStep] = Field(default_factory=list)
    stop_reason: str | None = None


class DirectTrace(BaseModel):
    case_id: str | None = None
    mode: Literal["direct"] = "direct"
    model: str | None = None
    provider: str | None = None
    model_parameters: dict[str, Any] = Field(default_factory=dict)
    input_event_count: int = 0
    model_turns: int = 0
    tool_calling_rounds: int = 0
    tool_calls: int = 0
    tool_call_details: list[dict[str, Any]] = Field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_latency_ms: float = 0.0
    stopped_by_round_limit: bool = False
    error: str | None = None
    errors: list[str] = Field(default_factory=list)
    primary_candidate_indices: list[int] = Field(default_factory=list)
    evidence_indices: list[int] = Field(default_factory=list)
    propagation_event_indices: list[int] = Field(default_factory=list)


class DirectRunResult(BaseModel):
    diagnosis: DirectDiagnosis | None = None
    trace: DirectTrace
