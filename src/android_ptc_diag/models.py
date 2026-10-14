from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LogEvent(BaseModel):
    source: Literal["logcat", "kmsg", "replay"]
    raw_line_number: int | None = Field(default=None, ge=1)
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
    target_component: str | None = None
    incident_time: datetime | None = None
    window_before_seconds: int | None = Field(default=None, ge=0)
    window_after_seconds: int | None = Field(default=None, ge=0)
    expected_behavior: str | None = None
    observed_behavior: str | None = None


CaseProvenance = Literal[
    "hand_authored_synthetic",
    "locally_reproduced_real_log",
]


class CaseMetadata(BaseModel):
    provenance: CaseProvenance | None = None


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


class DiagnosisClaim(BaseModel):
    """One evidence-grounded observation, inference, or debugging hypothesis."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    status: Literal["observation", "inference", "hypothesis"]
    evidence_refs: list[int]
    reasoning_summary: str | None = None
    confidence: Literal["low", "medium", "high"] | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    suggested_validation: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_claim_contract(self) -> DiagnosisClaim:
        if not self.evidence_refs:
            raise ValueError(f"{self.status} requires evidence_refs")
        if self.status == "inference" and not self.reasoning_summary:
            raise ValueError("inference requires reasoning_summary")
        if self.status == "hypothesis":
            if self.confidence is None:
                raise ValueError("hypothesis requires confidence")
            if not self.missing_evidence:
                raise ValueError("hypothesis requires missing_evidence")
            if not self.suggested_validation:
                raise ValueError("hypothesis requires suggested_validation")
        return self


class EvidenceGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    missing_evidence: list[str] = Field(min_length=1)


class EvidenceGroundedDiagnosis(BaseModel):
    """Evaluation contract separating facts, interpretations, and open hypotheses."""

    model_config = ConfigDict(extra="forbid")

    primary_candidate_indices: list[int] = Field(default_factory=list)
    evidence_indices: list[int] = Field(default_factory=list)
    propagation_event_indices: list[int] = Field(default_factory=list)
    claims: list[DiagnosisClaim] = Field(min_length=1)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    recommended_next_checks: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def validate_event_indices(self, event_count: int) -> None:
        if event_count < 0:
            raise ValueError("event_count must be non-negative")
        values = {
            "primary_candidate_indices": self.primary_candidate_indices,
            "evidence_indices": self.evidence_indices,
            "propagation_event_indices": self.propagation_event_indices,
        }
        for claim_index, claim in enumerate(self.claims):
            values[f"claims[{claim_index}].evidence_refs"] = claim.evidence_refs
        for field, indices in values.items():
            for index in indices:
                if index < 0 or index >= event_count:
                    raise ValueError(
                        f"invalid {field} index {index}; expected 0 <= index < {event_count}"
                    )


class EntityReference(BaseModel):
    """Evaluation-only canonical reference to replay-backed runtime state."""

    model_config = ConfigDict(extra="forbid")

    entity_type: Literal["process", "service", "mount"]
    entity_id: str
    canonical_name: str | None = None


class FollowupRequest(BaseModel):
    uncertainty_index: int
    check: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str


class DiagnosisLoopDecision(BaseModel):
    diagnosis: DirectDiagnosis
    followup_request: FollowupRequest | None = None


class DiagnosisLoopStep(BaseModel):
    current_diagnosis: DirectDiagnosis
    uncertainties: list[str] = Field(default_factory=list)
    uncertainty_index: int
    bound_uncertainty_text: str
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
