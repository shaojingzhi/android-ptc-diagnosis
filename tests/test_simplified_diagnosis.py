from pathlib import Path

import pytest
from pydantic import ValidationError

from android_ptc_diag.cases import load_runtime_state
from android_ptc_diag.models import SimplifiedClaim, SimplifiedDiagnosis


CASE_DIR = Path(__file__).parents[1] / "eval" / "cases" / "runtime_service_unready_001"


def _diagnosis(**overrides):
    values = {
        "claims": [
            {
                "statement": "runtime_service exceeded its readiness deadline",
                "type": "observation",
                "evidence_refs": [{"source_type": "log_event", "id": "event:1"}],
            },
            {
                "statement": "dependency initialization may delay readiness",
                "type": "hypothesis",
                "evidence_refs": [
                    {"source_type": "log_event", "id": "event:1"},
                    {
                        "source_type": "runtime_state",
                        "id": "service:runtime_service",
                    },
                ],
                "confidence": "medium",
                "missing_evidence": ["dependency startup state"],
            },
        ],
        "evidence_gaps": [
            {
                "question": "Why did runtime_service fail readiness?",
                "missing_evidence": ["dependency startup state"],
            }
        ],
        "recommended_next_checks": ["Inspect dependency state"],
    }
    values.update(overrides)
    return SimplifiedDiagnosis.model_validate(values)


def test_simplified_claim_requires_evidence_references():
    with pytest.raises(ValidationError):
        SimplifiedClaim(
            statement="runtime_service failed readiness",
            type="observation",
            evidence_refs=[],
        )


def test_simplified_claim_rejects_unsupported_type():
    with pytest.raises(ValidationError):
        SimplifiedClaim(
            statement="readiness propagated to clients",
            type="inference",
            evidence_refs=[{"source_type": "log_event", "id": "event:1"}],
        )


def test_simplified_hypothesis_metadata_is_optional():
    claim = SimplifiedClaim(
        statement="dependency startup may be delayed",
        type="hypothesis",
        evidence_refs=[{"source_type": "log_event", "id": "event:1"}],
    )

    assert claim.confidence is None
    assert claim.missing_evidence == []


@pytest.mark.parametrize("reference_id", ["event:not-an-index", "event:26"])
def test_simplified_diagnosis_rejects_invalid_event_reference(reference_id):
    diagnosis = _diagnosis(
        claims=[
            {
                "statement": "invalid event reference",
                "type": "observation",
                "evidence_refs": [
                    {"source_type": "log_event", "id": reference_id}
                ],
            }
        ]
    )

    with pytest.raises(ValueError, match="invalid claims.*log event reference"):
        diagnosis.validate_evidence_references(26, load_runtime_state(CASE_DIR))


@pytest.mark.parametrize(
    ("source_type", "reference_id"),
    [
        ("runtime_state", "service:vendor_bridge"),
        ("entity", "mount:/vendor"),
        ("entity", "cgroup:runtime_service"),
    ],
)
def test_simplified_diagnosis_rejects_unknown_runtime_reference(
    source_type, reference_id
):
    diagnosis = _diagnosis(
        claims=[
            {
                "statement": "invalid runtime reference",
                "type": "observation",
                "evidence_refs": [
                    {"source_type": source_type, "id": reference_id}
                ],
            }
        ]
    )

    with pytest.raises(ValueError, match="invalid claims"):
        diagnosis.validate_evidence_references(26, load_runtime_state(CASE_DIR))


def test_simplified_diagnosis_rejects_empty_evidence_gap():
    with pytest.raises(ValidationError):
        _diagnosis(
            evidence_gaps=[
                {
                    "question": "Why did readiness fail?",
                    "missing_evidence": [],
                }
            ]
        )


def test_simplified_diagnosis_accepts_mixed_valid_references():
    diagnosis = _diagnosis(
        claims=[
            {
                "statement": "runtime service is unready",
                "type": "observation",
                "evidence_refs": [
                    {"source_type": "log_event", "id": "event:1"},
                    {
                        "source_type": "runtime_state",
                        "id": "service:runtime_service",
                    },
                    {
                        "source_type": "entity",
                        "id": "process:runtime_service",
                    },
                    {
                        "source_type": "runtime_state",
                        "id": "cgroup:runtime_service",
                    },
                ],
            }
        ]
    )

    diagnosis.validate_evidence_references(26, load_runtime_state(CASE_DIR))
    assert len(diagnosis.claims[0].evidence_refs) == 4
