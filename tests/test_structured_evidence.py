from pathlib import Path

import pytest
from pydantic import ValidationError

from android_ptc_diag.cases import load_runtime_state
from android_ptc_diag.models import EvidenceItem, StructuredEvidenceDiagnosis


CASE_DIR = Path(__file__).parents[1] / "eval" / "cases" / "runtime_service_unready_001"


def _diagnosis(**overrides):
    values = {
        "evidence_items": [
            {
                "evidence_id": "event:1",
                "source_type": "log_event",
                "subject": {"type": "process", "id": "process:runtime_service"},
                "observation": {
                    "field": "message",
                    "value": "runtime service startup timeout",
                },
            },
            {
                "evidence_id": "service_state_001",
                "source_type": "runtime_state",
                "subject": {"type": "service", "id": "service:runtime_service"},
                "observation": {"field": "state", "value": "starting"},
            },
            {
                "evidence_id": "entity_service_001",
                "source_type": "entity",
                "subject": {"type": "service", "id": "service:runtime_service"},
            },
        ],
        "claims": [
            {
                "statement": "runtime_service exceeded its readiness deadline",
                "type": "observation",
                "evidence_refs": ["event:1", "service_state_001"],
            }
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
    return StructuredEvidenceDiagnosis.model_validate(values)


def _validate(diagnosis):
    diagnosis.validate_evidence_items(26, load_runtime_state(CASE_DIR))


def test_structured_evidence_accepts_supported_sources_and_references():
    diagnosis = _diagnosis()

    _validate(diagnosis)

    assert diagnosis.claims[0].evidence_refs == ["event:1", "service_state_001"]


def test_observed_evidence_requires_observation():
    with pytest.raises(ValidationError, match="requires an observation"):
        EvidenceItem.model_validate(
            {
                "evidence_id": "service_state_001",
                "source_type": "runtime_state",
                "subject": {"type": "service", "id": "service:runtime_service"},
            }
        )


def test_entity_evidence_may_omit_observation():
    item = EvidenceItem.model_validate(
        {
            "evidence_id": "entity_service_001",
            "source_type": "entity",
            "subject": {"type": "service", "id": "service:runtime_service"},
        }
    )

    assert item.observation is None


@pytest.mark.parametrize(
    "subject",
    [
        {"type": "service", "id": "process:runtime_service"},
        {"type": "event", "id": "event:not-an-index"},
    ],
)
def test_evidence_item_rejects_malformed_subject(subject):
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate(
            {
                "evidence_id": "event:1",
                "source_type": "log_event",
                "subject": subject,
                "observation": {"field": "message", "value": "timeout"},
            }
        )


def test_structured_diagnosis_rejects_unknown_evidence_reference():
    diagnosis = _diagnosis(
        claims=[
            {
                "statement": "unknown evidence",
                "type": "observation",
                "evidence_refs": ["missing_item"],
            }
        ]
    )

    with pytest.raises(ValueError, match="unknown claims.*missing_item"):
        _validate(diagnosis)


def test_structured_diagnosis_rejects_duplicate_evidence_id():
    diagnosis = _diagnosis()
    diagnosis.evidence_items.append(diagnosis.evidence_items[0])

    with pytest.raises(ValueError, match="duplicate evidence_id"):
        _validate(diagnosis)


@pytest.mark.parametrize(
    "item",
    [
        {
            "evidence_id": "event:26",
            "source_type": "log_event",
            "subject": {"type": "event", "id": "event:26"},
            "observation": {"field": "message", "value": "timeout"},
        },
        {
            "evidence_id": "service_state_001",
            "source_type": "runtime_state",
            "subject": {"type": "service", "id": "service:missing"},
            "observation": {"field": "state", "value": "starting"},
        },
        {
            "evidence_id": "entity_cgroup_001",
            "source_type": "entity",
            "subject": {"type": "cgroup", "id": "cgroup:missing"},
        },
    ],
)
def test_structured_diagnosis_rejects_invalid_subject_identity(item):
    diagnosis = _diagnosis(evidence_items=[item])

    with pytest.raises(ValueError, match="invalid evidence_items"):
        _validate(diagnosis)


def test_structured_diagnosis_rejects_malformed_source():
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate(
            {
                "evidence_id": "service_state_001",
                "source_type": "filesystem",
                "subject": {"type": "service", "id": "service:runtime_service"},
                "observation": {"field": "state", "value": "starting"},
            }
        )


def test_structured_diagnosis_accepts_canonical_cgroup_entity():
    diagnosis = _diagnosis(
        evidence_items=[
            {
                "evidence_id": "entity:cgroup:runtime_service",
                "source_type": "entity",
                "subject": {
                    "type": "cgroup",
                    "id": "cgroup:runtime_service",
                },
            }
        ],
        claims=[
            {
                "statement": "the cgroup identity was shown",
                "type": "observation",
                "evidence_refs": ["entity:cgroup:runtime_service"],
            }
        ],
    )

    _validate(diagnosis)


def test_existing_simplified_contract_remains_separate():
    diagnosis = _diagnosis()

    assert "evidence_items" in diagnosis.model_dump()
    assert "source_type" not in diagnosis.claims[0].model_dump()["evidence_refs"][0]
