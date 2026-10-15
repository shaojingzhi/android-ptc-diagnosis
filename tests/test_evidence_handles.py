import json

import pytest

from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.compact import build_compact_evidence, compact_model_view
from android_ptc_diag.evidence_registry import EvidenceReferenceError, EvidenceRegistry
from android_ptc_diag.execution import RegistryBackedCompactDirectExecutor
from android_ptc_diag.models import LogEvent, RegistryBackedDiagnosis, TriageRequest
from android_ptc_diag.primitives import get_events


class FakeResponse:
    def __init__(self, *, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.messages.append(messages)
        return next(self.responses)


def _events():
    events = [
        LogEvent(source="replay", level="I", message="service starting"),
        LogEvent(source="replay", level="E", message="readiness deadline exceeded"),
    ]
    cluster_events(events)
    return events


def _diagnosis(reference):
    return {
        "claims": [
            {
                "statement": "The service exceeded its readiness deadline.",
                "type": "observation",
                "evidence_refs": [reference],
            }
        ],
        "evidence_gaps": [],
        "recommended_next_checks": [],
    }


def _request():
    return TriageRequest(symptom="runtime service is not ready")


def test_short_handles_are_stable_unique_and_run_local():
    events = _events()
    compact = compact_model_view(build_compact_evidence(events))
    registry = EvidenceRegistry()
    compact_items = registry.add_compact_view(compact)
    assert registry.handle_map() == {}

    first_view = registry.model_items(compact_items)
    assert [item["handle"] for item in first_view] == ["E001", "E002"]
    assert all("evidence_id" not in item for item in first_view)
    assert first_view[0]["provenance"] == [
        compact_items[0].provenance[0].model_dump(mode="json")
    ]

    repeated = registry.model_items(registry.add_compact_view(compact))
    assert [item["handle"] for item in repeated] == ["E001", "E002"]

    tool_items = registry.add_tool_result(
        tool_call_id="call-1",
        tool_name="get_events",
        result=get_events(events, indices=[1]),
    )
    assert [item["handle"] for item in registry.model_items(tool_items)] == ["E003"]
    assert registry.handle_map()["E001"] == compact_items[0].evidence_id

    separate_registry = EvidenceRegistry()
    separate_items = separate_registry.add_compact_view(compact)
    assert separate_registry.model_items(separate_items)[0]["handle"] == "E001"


def test_short_handles_resolve_strictly_with_precise_error_paths():
    registry = EvidenceRegistry()
    items = registry.add_compact_view(
        compact_model_view(build_compact_evidence(_events()))
    )
    registry.model_items(items)

    resolved = registry.resolve_references(
        RegistryBackedDiagnosis.model_validate(_diagnosis("E002"))
    )
    assert resolved.claims[0].evidence_refs == [items[1].evidence_id]
    registry.validate_references(resolved)

    invalid = RegistryBackedDiagnosis.model_validate(_diagnosis("E999"))
    with pytest.raises(EvidenceReferenceError) as error:
        registry.resolve_references(invalid)
    assert error.value.path == ["claims", 0, "evidence_refs", 0]
    assert str(error.value) == "unknown evidence handle: E999"


def test_hardened_initial_evidence_uses_handles_and_returns_canonical_ids():
    events = _events()
    canonical_items = EvidenceRegistry().add_compact_view(
        compact_model_view(build_compact_evidence(events))
    )
    model = FakeModel([FakeResponse(content=json.dumps(_diagnosis("E002")))])

    result = RegistryBackedCompactDirectExecutor(
        model, events, reliability_hardening=True
    ).run(_request())

    canonical_id = canonical_items[1].evidence_id
    assert result.trace.final_status == "accepted"
    assert result.diagnosis.claims[0].evidence_refs == [canonical_id]
    assert result.trace.final_parsed_payload["claims"][0]["evidence_refs"] == [
        "E002"
    ]
    assert result.trace.final_resolved_payload["claims"][0]["evidence_refs"] == [
        canonical_id
    ]
    assert result.trace.evidence_handle_map["E002"] == canonical_id
    assert result.trace.finalization_fallback_triggered is False
    assert result.trace.model_turns == 1

    prompt = json.loads(model.messages[0][1]["content"].rsplit("\n", 1)[-1])
    assert [item["handle"] for item in prompt["evidence_registry"]] == [
        "E001",
        "E002",
    ]
    assert all("evidence_id" not in item for item in prompt["evidence_registry"])


def test_hardened_tool_evidence_uses_the_same_handle_space():
    events = _events()
    expected_item = next(
        item
        for item in EvidenceRegistry().add_tool_result(
            tool_call_id="call-1",
            tool_name="get_events",
            result=get_events(events, indices=[1]),
        )
        if item.observation.field == "normalized_event"
    )
    model = FakeModel(
        [
            FakeResponse(
                tool_calls=[
                    {"id": "call-1", "name": "get_events", "args": {"indices": [1]}}
                ]
            ),
            FakeResponse(content=json.dumps(_diagnosis("E003"))),
        ]
    )

    result = RegistryBackedCompactDirectExecutor(
        model, events, reliability_hardening=True
    ).run(_request())

    tool_message = model.messages[-1][-1]
    content = tool_message["content"] if isinstance(tool_message, dict) else tool_message.content
    visible = json.loads(content)["_system_evidence_items"]
    assert [item["handle"] for item in visible] == ["E003"]
    assert "evidence_id" not in visible[0]
    assert result.diagnosis.claims[0].evidence_refs == [expected_item.evidence_id]
