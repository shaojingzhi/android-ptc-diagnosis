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
    "locally_reproduced",
    "public_case_adapted",
    "synthetic",
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


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["log_event", "runtime_state", "entity"]
    id: str = Field(min_length=1)


class SimplifiedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    type: Literal["observation", "hypothesis"]
    evidence_refs: list[EvidenceReference] = Field(min_length=1)
    confidence: Literal["low", "medium", "high"] | None = None
    missing_evidence: list[str] = Field(default_factory=list)


class SimplifiedDiagnosis(BaseModel):
    """Minimal evidence-grounded output for evaluation."""

    model_config = ConfigDict(extra="forbid")

    claims: list[SimplifiedClaim] = Field(min_length=1)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    recommended_next_checks: list[str] = Field(default_factory=list)

    def validate_evidence_references(
        self, event_count: int, runtime_state: dict[str, Any]
    ) -> None:
        if event_count < 0:
            raise ValueError("event_count must be non-negative")
        collections = {
            "process": "processes",
            "service": "services",
            "mount": "mounts",
            "cgroup": "cgroups",
        }
        runtime_ids = {
            f"{entity_type}:{identifier}"
            for entity_type, collection in collections.items()
            for identifier in _runtime_identifiers(runtime_state, collection, entity_type)
        }
        entity_ids = set(runtime_ids)

        for claim_index, claim in enumerate(self.claims):
            for reference in claim.evidence_refs:
                field = f"claims[{claim_index}].evidence_refs"
                if reference.source_type == "log_event":
                    prefix, separator, value = reference.id.partition(":")
                    if prefix != "event" or not separator or not value.isdigit():
                        raise ValueError(f"invalid {field} log event reference: {reference.id}")
                    index = int(value)
                    if index >= event_count:
                        raise ValueError(
                            f"invalid {field} log event reference: {reference.id}"
                        )
                elif reference.source_type == "runtime_state":
                    if reference.id not in runtime_ids:
                        raise ValueError(
                            f"invalid {field} runtime state reference: {reference.id}"
                        )
                elif reference.id not in entity_ids:
                    raise ValueError(f"invalid {field} entity reference: {reference.id}")


class EvidenceSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["event", "process", "service", "mount", "cgroup"]
    id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_identity_format(self) -> EvidenceSubject:
        prefix, separator, value = self.id.partition(":")
        if prefix != self.type or not separator or not value:
            raise ValueError(
                f"subject id must use the '{self.type}:<identifier>' format"
            )
        if self.type == "event" and not value.isdigit():
            raise ValueError("event subject id must contain a numeric index")
        return self


class EvidenceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    value: Any


class EvidenceProvenance(BaseModel):
    """Deterministic pointer back to model-visible source data."""

    model_config = ConfigDict(extra="forbid")

    origin: Literal["compact_group", "tool_result"]
    event_index: int | None = None
    group_id: int | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    result_path: list[str | int] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    """Typed, inspectable evidence without semantic-entailment claims."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    source_type: Literal["log_event", "runtime_state", "entity"]
    subject: EvidenceSubject
    observation: EvidenceObservation | None = None
    provenance: list[EvidenceProvenance] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_observation_for_observed_sources(self) -> EvidenceItem:
        if self.source_type in {"log_event", "runtime_state"} and self.observation is None:
            raise ValueError(f"{self.source_type} evidence requires an observation")
        return self


class EvidenceItemClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    type: Literal["observation", "hypothesis"]
    evidence_refs: list[str] = Field(min_length=1)
    confidence: Literal["low", "medium", "high"] | None = None
    missing_evidence: list[str] = Field(default_factory=list)


class StructuredEvidenceDiagnosis(BaseModel):
    """Simplified diagnosis with an additive EvidenceItem registry."""

    model_config = ConfigDict(extra="forbid")

    evidence_items: list[EvidenceItem] = Field(min_length=1)
    claims: list[EvidenceItemClaim] = Field(min_length=1)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    recommended_next_checks: list[str] = Field(default_factory=list)

    def validate_evidence_items(
        self, event_count: int, runtime_state: dict[str, Any]
    ) -> None:
        if event_count < 0:
            raise ValueError("event_count must be non-negative")
        evidence_by_id: dict[str, EvidenceItem] = {}
        for item_index, item in enumerate(self.evidence_items):
            if item.evidence_id in evidence_by_id:
                raise ValueError(f"duplicate evidence_id: {item.evidence_id}")
            evidence_by_id[item.evidence_id] = item
            self._validate_item_identity(item, item_index, event_count, runtime_state)

        for claim_index, claim in enumerate(self.claims):
            for evidence_id in claim.evidence_refs:
                if evidence_id not in evidence_by_id:
                    raise ValueError(
                        f"unknown claims[{claim_index}].evidence_refs id: {evidence_id}"
                    )

    @staticmethod
    def _validate_item_identity(
        item: EvidenceItem,
        item_index: int,
        event_count: int,
        runtime_state: dict[str, Any],
    ) -> None:
        field = f"evidence_items[{item_index}]"
        subject_type = item.subject.type
        subject_id = item.subject.id
        if item.source_type == "log_event":
            prefix, separator, value = item.evidence_id.partition(":")
            if prefix != "event" or not separator or not value.isdigit():
                raise ValueError(f"invalid {field} log event evidence_id: {item.evidence_id}")
            if int(value) >= event_count:
                raise ValueError(f"invalid {field} log event evidence_id: {item.evidence_id}")
            if subject_type == "event" and subject_id != item.evidence_id:
                raise ValueError(f"invalid {field} event subject identity: {subject_id}")
            if subject_type != "event":
                _validate_runtime_subject(subject_type, subject_id, runtime_state, field)
            return

        if subject_type == "event":
            raise ValueError(f"invalid {field} subject for {item.source_type}: {subject_id}")
        _validate_runtime_subject(subject_type, subject_id, runtime_state, field)


class RegistryBackedDiagnosis(BaseModel):
    """Diagnosis whose evidence items are supplied and owned by the system."""

    model_config = ConfigDict(extra="forbid")

    claims: list[EvidenceItemClaim] = Field(min_length=1)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    recommended_next_checks: list[str] = Field(default_factory=list)


def _validate_runtime_subject(
    subject_type: str,
    subject_id: str,
    runtime_state: dict[str, Any],
    field: str,
) -> None:
    collections = {
        "process": "processes",
        "service": "services",
        "mount": "mounts",
        "cgroup": "cgroups",
    }
    collection = collections.get(subject_type)
    if collection is None:
        raise ValueError(f"invalid {field} subject identity: {subject_id}")
    identifier = subject_id.partition(":")[2]
    if identifier not in _runtime_identifiers(runtime_state, collection, subject_type):
        raise ValueError(f"invalid {field} subject identity: {subject_id}")


def _runtime_identifiers(
    runtime_state: dict[str, Any], collection: str, entity_type: str
) -> list[str]:
    entries = runtime_state.get(collection, {})
    if not isinstance(entries, dict):
        raise ValueError(f"invalid {entity_type} state collection")
    return list(entries)


class EntityReference(BaseModel):
    """Evaluation-only canonical reference to replay-backed runtime state."""

    model_config = ConfigDict(extra="forbid")

    entity_type: Literal["process", "service", "mount", "cgroup"]
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


class FinalValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Literal["json_parse", "schema_validation", "reference_validation"]
    message: str
    path: list[str | int] = Field(default_factory=list)


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
    model_call_details: list[dict[str, Any]] = Field(default_factory=list)
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
    final_status: Literal[
        "not_returned",
        "json_parse_failed",
        "schema_validation_failed",
        "reference_validation_failed",
        "accepted",
    ] | None = None
    final_raw_text: str | None = None
    final_parsed_payload: Any | None = None
    final_resolved_payload: Any | None = None
    final_validation_errors: list[FinalValidationIssue] = Field(default_factory=list)
    evidence_handle_map: dict[str, str] = Field(default_factory=dict)
    finalization_fallback_triggered: bool = False
    initial_non_json_response: str | None = None
    finalization_trigger_status: Literal["json_parse_failed"] | None = None
    finalization_trigger_error: str | None = None
    finalization_response: str | None = None
    finalization_model_turn: int | None = None
    stop_reason: str | None = None
    evidence_registry: list[EvidenceItem] = Field(default_factory=list)


class DirectRunResult(BaseModel):
    diagnosis: DirectDiagnosis | None = None
    trace: DirectTrace


class RegistryBackedRunResult(BaseModel):
    diagnosis: RegistryBackedDiagnosis | None = None
    evidence_registry: list[EvidenceItem] = Field(default_factory=list)
    trace: DirectTrace
