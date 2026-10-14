import pytest
from pydantic import ValidationError

from android_ptc_diag.models import (
    DiagnosisClaim,
    EvidenceGap,
    EvidenceGroundedDiagnosis,
)


def _diagnosis(**overrides):
    values = {
        "primary_candidate_indices": [1],
        "evidence_indices": [1, 2],
        "propagation_event_indices": [2],
        "claims": [
            {
                "statement": "runtime service exceeded its readiness deadline",
                "status": "observation",
                "evidence_refs": [1],
            },
            {
                "statement": "dependency initialization may delay readiness",
                "status": "hypothesis",
                "evidence_refs": [1, 2],
                "confidence": "medium",
                "missing_evidence": ["dependency readiness state"],
                "suggested_validation": ["inspect dependency state"],
            },
        ],
        "evidence_gaps": [
            {
                "question": "Why did the runtime service fail readiness?",
                "missing_evidence": ["dependency readiness state"],
            }
        ],
    }
    values.update(overrides)
    return EvidenceGroundedDiagnosis.model_validate(values)


def test_observation_requires_evidence_references():
    with pytest.raises(ValidationError, match="observation requires evidence_refs"):
        DiagnosisClaim(
            statement="runtime service is starting",
            status="observation",
            evidence_refs=[],
        )


def test_inference_requires_evidence_and_reasoning_summary():
    with pytest.raises(ValidationError, match="inference requires reasoning_summary"):
        DiagnosisClaim(
            statement="readiness failure propagated to clients",
            status="inference",
            evidence_refs=[1, 2],
        )


@pytest.mark.parametrize("missing_field", ["confidence", "missing_evidence", "suggested_validation"])
def test_hypothesis_requires_validation_metadata(missing_field):
    values = {
        "statement": "dependency startup may be delayed",
        "status": "hypothesis",
        "evidence_refs": [1],
        "confidence": "low",
        "missing_evidence": ["dependency state"],
        "suggested_validation": ["inspect dependency state"],
    }
    values.pop(missing_field)

    with pytest.raises(ValidationError, match=f"hypothesis requires {missing_field}"):
        DiagnosisClaim.model_validate(values)


def test_evidence_gap_requires_missing_evidence():
    with pytest.raises(ValidationError):
        EvidenceGap(question="Why did readiness fail?", missing_evidence=[])


def test_diagnosis_rejects_out_of_range_claim_evidence_reference():
    diagnosis = _diagnosis()
    diagnosis.claims[1].evidence_refs.append(99)

    with pytest.raises(ValueError, match=r"claims\[1\]\.evidence_refs index 99"):
        diagnosis.validate_event_indices(3)


def test_valid_grounded_diagnosis_preserves_useful_hypothesis():
    diagnosis = _diagnosis()

    diagnosis.validate_event_indices(3)
    hypothesis = diagnosis.claims[1]
    assert hypothesis.status == "hypothesis"
    assert hypothesis.confidence == "medium"
    assert hypothesis.missing_evidence == ["dependency readiness state"]
    assert hypothesis.suggested_validation == ["inspect dependency state"]
    assert "root_cause" not in diagnosis.model_dump()
