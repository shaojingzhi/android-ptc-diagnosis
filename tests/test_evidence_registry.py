import json

import pytest

from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.compact import build_compact_evidence, compact_model_view
from android_ptc_diag.evidence_registry import EvidenceReferenceError, EvidenceRegistry
from android_ptc_diag.execution import RegistryBackedCompactDirectExecutor
from android_ptc_diag.models import LogEvent, RegistryBackedDiagnosis, TriageRequest
from android_ptc_diag.primitives import (
    discover_runtime_entities,
    get_events,
    inspect_cgroup_state_by_ref,
    inspect_process_state_by_ref,
    inspect_service_state,
)


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
        LogEvent(
            source="replay",
            level="W",
            pid=10,
            tag="RuntimeService",
            message="service starting",
        ),
        LogEvent(
            source="replay",
            level="E",
            pid=10,
            tag="RuntimeService",
            message="readiness deadline exceeded",
        ),
    ]
    cluster_events(events)
    return events


def _request():
    return TriageRequest(symptom="runtime service is not ready")


def _diagnosis(evidence_id):
    return {
        "claims": [
            {
                "statement": "The runtime service exceeded its readiness deadline.",
                "type": "observation",
                "evidence_refs": [evidence_id],
            },
            {
                "statement": "A dependency may have delayed readiness.",
                "type": "hypothesis",
                "evidence_refs": [evidence_id],
                "confidence": "low",
                "missing_evidence": ["dependency state"],
            },
        ],
        "evidence_gaps": [
            {
                "question": "Why was readiness delayed?",
                "missing_evidence": ["dependency state"],
            }
        ],
        "recommended_next_checks": ["Inspect dependency readiness state"],
    }


def _initial_evidence_id(events):
    view = compact_model_view(build_compact_evidence(events))
    return EvidenceRegistry().add_compact_view(view)[1].evidence_id


def test_compact_registry_items_preserve_visible_group_values_and_provenance():
    events = _events()
    view = compact_model_view(build_compact_evidence(events))
    registry = EvidenceRegistry()

    items = registry.add_compact_view(view)

    assert len(items) == 2
    item = items[1]
    assert item.source_type == "log_event"
    assert item.subject.id == "event:1"
    assert item.observation.field == "compact_group"
    assert item.observation.value == view["groups"][1]
    assert item.provenance[0].event_index == 1
    assert item.provenance[0].group_id == 1
    assert item.provenance[0].result_path == ["compact_evidence", "groups", 1]


def test_log_tool_result_uses_event_subject_without_guessing_component_identity():
    events = _events()
    result = get_events(events, indices=[1])
    registry = EvidenceRegistry()

    items = registry.add_tool_result(
        tool_call_id="call-1", tool_name="get_events", result=result
    )

    item = next(item for item in items if item.observation.field == "normalized_event")
    assert item.subject.id == "event:1"
    assert item.observation.value["message"] == events[1].message
    assert item.provenance[0].tool_call_id == "call-1"
    assert item.provenance[0].result_path == ["events", 0]


def test_runtime_tool_result_generates_faithful_state_and_entity_evidence():
    state = {
        "services": {
            "runtime_service": {"state": "starting", "ready": False},
            "unqueried_service": {"state": "running", "ready": True},
        }
    }
    result = inspect_service_state(state, name="runtime_service")
    registry = EvidenceRegistry()

    items = registry.add_tool_result(
        tool_call_id="call-service",
        tool_name="inspect_service_state",
        result=result,
    )

    observations = {
        item.observation.field: item.observation.value
        for item in items
        if item.source_type == "runtime_state"
    }
    entity = next(item for item in items if item.source_type == "entity")
    assert observations == {"state": "starting", "ready": False}
    assert entity.subject.id == "service:runtime_service"
    assert entity.observation is None
    assert all("unqueried_service" not in item.evidence_id for item in items)


def test_cgroup_discovery_registers_identity_without_unqueried_state():
    state = {
        "cgroups": {
            "scope_record_7": {
                "path": "/sys/fs/cgroup/demo/7",
                "memory_max_bytes": 33554432,
            }
        }
    }
    result = discover_runtime_entities(state, entity_type="cgroup")

    items = EvidenceRegistry().add_tool_result(
        tool_call_id="discover-cgroup",
        tool_name="discover_runtime_entities",
        result=result,
    )

    assert [item.evidence_id for item in items] == ["entity:cgroup:scope_record_7"]
    assert items[0].source_type == "entity"
    assert items[0].subject.id == "cgroup:scope_record_7"
    assert items[0].observation is None
    assert items[0].provenance[0].result_path == ["entities", 0, "entity_ref"]


def test_process_cgroup_link_and_inspection_keep_identity_and_state_boundaries():
    state = {
        "processes": {
            "bootstrap_worker": {
                "pid": 71,
                "state": "running",
                "cgroup_path": "/sys/fs/cgroup/demo/7",
            }
        },
        "cgroups": {
            "scope_record_7": {
                "path": "/sys/fs/cgroup/demo/7",
                "memory_max_bytes": 33554432,
            }
        },
    }
    process_ref = {
        "entity_type": "process",
        "entity_id": "process:bootstrap_worker",
        "canonical_name": "bootstrap_worker",
    }
    cgroup_ref = {
        "entity_type": "cgroup",
        "entity_id": "cgroup:scope_record_7",
        "canonical_name": "scope_record_7",
    }
    registry = EvidenceRegistry()

    process_items = registry.add_tool_result(
        tool_call_id="inspect-process",
        tool_name="inspect_process_state_by_ref",
        result=inspect_process_state_by_ref(state, entity_ref=process_ref),
    )

    linked = next(item for item in process_items if item.subject.type == "cgroup")
    assert linked.evidence_id == "entity:cgroup:scope_record_7"
    assert linked.source_type == "entity"
    assert linked.observation is None
    assert linked.provenance[0].result_path == ["cgroup_ref"]
    assert not any(
        item.subject.type == "cgroup" and item.source_type == "runtime_state"
        for item in process_items
    )

    cgroup_items = registry.add_tool_result(
        tool_call_id="inspect-cgroup",
        tool_name="inspect_cgroup_state_by_ref",
        result=inspect_cgroup_state_by_ref(state, entity_ref=cgroup_ref),
    )

    identity = next(item for item in cgroup_items if item.source_type == "entity")
    limit = next(
        item
        for item in cgroup_items
        if item.observation and item.observation.field == "memory_max_bytes"
    )
    assert identity.evidence_id == linked.evidence_id
    assert len(identity.provenance) == 2
    assert identity.provenance[-1].result_path == ["entity_ref"]
    assert limit.subject.id == "cgroup:scope_record_7"
    assert limit.observation.value == 33554432
    assert limit.provenance[0].result_path == ["memory_max_bytes"]


def test_registry_ids_are_stable_deduplicated_and_run_local():
    result = get_events(_events(), indices=[1])
    registry = EvidenceRegistry()
    first = registry.add_tool_result(
        tool_call_id="call-1", tool_name="get_events", result=result
    )
    second = registry.add_tool_result(
        tool_call_id="call-2", tool_name="get_events", result=result
    )

    assert [item.evidence_id for item in first] == [
        item.evidence_id for item in second
    ]
    assert len(registry.snapshot()) == len(first)
    assert all(len(item.provenance) == 2 for item in registry.snapshot())
    assert EvidenceRegistry().snapshot() == []


def test_registry_accepts_provided_id_and_rejects_unknown_id_with_path():
    events = _events()
    registry = EvidenceRegistry()
    evidence_id = registry.add_compact_view(
        compact_model_view(build_compact_evidence(events))
    )[0].evidence_id
    accepted = RegistryBackedDiagnosis.model_validate(_diagnosis(evidence_id))
    rejected = RegistryBackedDiagnosis.model_validate(_diagnosis("unknown:evidence"))

    registry.validate_references(accepted)
    with pytest.raises(EvidenceReferenceError) as error:
        registry.validate_references(rejected)

    assert error.value.path == ["claims", 0, "evidence_refs", 0]


def test_registry_backed_executor_final_contains_references_not_registry():
    events = _events()
    evidence_id = _initial_evidence_id(events)
    model = FakeModel([FakeResponse(content=json.dumps(_diagnosis(evidence_id)))])

    result = RegistryBackedCompactDirectExecutor(model, events).run(_request())

    assert result.diagnosis is not None
    assert result.trace.final_status == "accepted"
    assert result.diagnosis.claims[0].evidence_refs == [evidence_id]
    assert "evidence_items" not in result.diagnosis.model_dump()
    assert result.evidence_registry == result.trace.evidence_registry
    prompt = json.loads(model.messages[0][1]["content"].rsplit("\n", 1)[-1])
    assert prompt["evidence_registry"]
    assert "evidence_items" not in prompt["output_schema"]["properties"]


def test_tool_evidence_is_registered_and_supplied_before_final_reference():
    events = _events()
    tool_result = get_events(events, indices=[1])
    expected_registry = EvidenceRegistry()
    expected_item = next(
        item
        for item in expected_registry.add_tool_result(
            tool_call_id="call-1", tool_name="get_events", result=tool_result
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
            FakeResponse(content=json.dumps(_diagnosis(expected_item.evidence_id))),
        ]
    )

    result = RegistryBackedCompactDirectExecutor(model, events).run(_request())

    assert result.diagnosis is not None
    tool_message = model.messages[1][-1]
    content = (
        tool_message["content"]
        if isinstance(tool_message, dict)
        else tool_message.content
    )
    payload = json.loads(content)
    supplied_ids = {
        item["evidence_id"] for item in payload["_system_evidence_items"]
    }
    assert expected_item.evidence_id in supplied_ids
    assert expected_item.evidence_id in {
        item.evidence_id for item in result.evidence_registry
    }


def test_unknown_final_reference_is_rejected_but_fully_auditable():
    raw = json.dumps(_diagnosis("unknown:evidence"))
    result = RegistryBackedCompactDirectExecutor(
        FakeModel([FakeResponse(content=raw)]), _events()
    ).run(_request())

    assert result.diagnosis is None
    assert result.trace.final_status == "reference_validation_failed"
    assert result.trace.final_raw_text == raw
    assert result.trace.final_parsed_payload == json.loads(raw)
    issue = result.trace.final_validation_errors[0]
    assert issue.stage == "reference_validation"
    assert issue.path == ["claims", 0, "evidence_refs", 0]
    assert "unknown evidence id" in issue.message


def test_typed_runtime_query_supplies_system_id_and_accepts_final_reference():
    events = _events()
    runtime_state = {
        "services": {
            "runtime_service": {"state": "starting", "ready": False},
        },
        "processes": {
            "unqueried_process": {"pid": 99, "state": "running"},
        },
        "cgroups": {},
        "mounts": {},
    }
    service_ref = {
        "entity_type": "service",
        "entity_id": "service:runtime_service",
        "canonical_name": "runtime_service",
    }
    service_result = inspect_service_state(runtime_state, name="runtime_service")
    expected_item = next(
        item
        for item in EvidenceRegistry().add_tool_result(
            tool_call_id="inspect-service",
            tool_name="inspect_service_state_by_ref",
            result=service_result,
        )
        if item.observation and item.observation.field == "ready"
    )
    model = FakeModel(
        [
            FakeResponse(
                tool_calls=[
                    {
                        "id": "discover-service",
                        "name": "discover_runtime_entities",
                        "args": {"entity_type": "service"},
                    }
                ]
            ),
            FakeResponse(
                tool_calls=[
                    {
                        "id": "inspect-service",
                        "name": "inspect_service_state_by_ref",
                        "args": {"entity_ref": service_ref},
                    }
                ]
            ),
            FakeResponse(content=json.dumps(_diagnosis(expected_item.evidence_id))),
        ]
    )

    result = RegistryBackedCompactDirectExecutor(
        model, events, runtime_state=runtime_state
    ).run(_request())

    assert result.trace.final_status == "accepted"
    assert result.diagnosis is not None
    assert [detail["tool"] for detail in result.trace.tool_call_details] == [
        "discover_runtime_entities",
        "inspect_service_state_by_ref",
    ]
    assert len(result.trace.model_call_details) == 3
    assert result.trace.model_call_details[-1]["returned_final"] is True
    registry = {item.evidence_id: item for item in result.evidence_registry}
    assert expected_item.evidence_id in registry
    assert all("unqueried_process" not in item.subject.id for item in registry.values())

    tool_payloads = []
    for message in model.messages[-1]:
        content = (
            message.get("content")
            if isinstance(message, dict)
            else getattr(message, "content", None)
        )
        if isinstance(content, str) and content.startswith("{"):
            payload = json.loads(content)
            if "_system_evidence_items" in payload:
                tool_payloads.append(payload)
    supplied_ids = {
        item["evidence_id"]
        for payload in tool_payloads
        for item in payload["_system_evidence_items"]
    }
    assert expected_item.evidence_id in supplied_ids
    assert "evidence_items" not in result.trace.final_parsed_payload


def test_process_to_cgroup_typed_query_supplies_state_id_and_accepts_final_reference():
    events = _events()
    runtime_state = {
        "processes": {
            "bootstrap_worker": {
                "pid": 71,
                "state": "running",
                "cgroup_path": "/sys/fs/cgroup/demo/7",
            }
        },
        "cgroups": {
            "scope_record_7": {
                "path": "/sys/fs/cgroup/demo/7",
                "memory_max_bytes": 33554432,
            }
        },
        "services": {},
        "mounts": {},
    }
    process_ref = {
        "entity_type": "process",
        "entity_id": "process:bootstrap_worker",
        "canonical_name": "bootstrap_worker",
    }
    cgroup_ref = {
        "entity_type": "cgroup",
        "entity_id": "cgroup:scope_record_7",
        "canonical_name": "scope_record_7",
    }
    cgroup_result = inspect_cgroup_state_by_ref(
        runtime_state, entity_ref=cgroup_ref
    )
    expected_item = next(
        item
        for item in EvidenceRegistry().add_tool_result(
            tool_call_id="inspect-cgroup",
            tool_name="inspect_cgroup_state_by_ref",
            result=cgroup_result,
        )
        if item.observation and item.observation.field == "memory_max_bytes"
    )
    model = FakeModel(
        [
            FakeResponse(
                tool_calls=[
                    {
                        "id": "discover-process",
                        "name": "discover_runtime_entities",
                        "args": {"entity_type": "process"},
                    }
                ]
            ),
            FakeResponse(
                tool_calls=[
                    {
                        "id": "inspect-process",
                        "name": "inspect_process_state_by_ref",
                        "args": {"entity_ref": process_ref},
                    }
                ]
            ),
            FakeResponse(
                tool_calls=[
                    {
                        "id": "inspect-cgroup",
                        "name": "inspect_cgroup_state_by_ref",
                        "args": {"entity_ref": cgroup_ref},
                    }
                ]
            ),
            FakeResponse(content=json.dumps(_diagnosis(expected_item.evidence_id))),
        ]
    )

    result = RegistryBackedCompactDirectExecutor(
        model, events, runtime_state=runtime_state
    ).run(_request())

    assert result.trace.final_status == "accepted"
    assert [detail["tool"] for detail in result.trace.tool_call_details] == [
        "discover_runtime_entities",
        "inspect_process_state_by_ref",
        "inspect_cgroup_state_by_ref",
    ]
    tool_payloads = {
        message.tool_call_id: json.loads(message.content)
        for message in model.messages[-1]
        if getattr(message, "tool_call_id", None)
    }
    process_payload = tool_payloads["inspect-process"]
    assert process_payload["cgroup_ref"] == cgroup_ref
    cgroup_payload = tool_payloads["inspect-cgroup"]
    supplied_ids = {
        item["evidence_id"] for item in cgroup_payload["_system_evidence_items"]
    }
    assert expected_item.evidence_id in supplied_ids
    registry = {item.evidence_id: item for item in result.evidence_registry}
    assert registry[expected_item.evidence_id].observation.value == 33554432
