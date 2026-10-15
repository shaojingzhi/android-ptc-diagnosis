import json

import pytest

from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.execution import RegistryBackedCompactDirectExecutor
from android_ptc_diag.models import LogEvent, TriageRequest


class FakeResponse:
    def __init__(
        self,
        *,
        content="",
        tool_calls=None,
        input_tokens=None,
        output_tokens=None,
    ):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage_metadata = (
            {"input_tokens": input_tokens, "output_tokens": output_tokens}
            if input_tokens is not None and output_tokens is not None
            else None
        )


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.messages.append(list(messages))
        return next(self.responses)


def _events():
    events = [
        LogEvent(source="replay", level="I", message="service starting"),
        LogEvent(source="replay", level="E", message="readiness deadline exceeded"),
    ]
    cluster_events(events)
    return events


def _request():
    return TriageRequest(symptom="runtime service is not ready")


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


def _tool_payload(message):
    content = message["content"] if isinstance(message, dict) else message.content
    return json.loads(content)


def _path_value(value, path):
    for part in path:
        value = value[part]
    return value


def _executor(model, *, runtime_state=None):
    return RegistryBackedCompactDirectExecutor(
        model,
        _events(),
        runtime_state=runtime_state,
        reliability_hardening=True,
        deduplicated_evidence_view=True,
    )


def test_lean_initial_groups_carry_handles_without_registry_copy():
    model = FakeModel([FakeResponse(content=json.dumps(_diagnosis("E002")))])
    result = _executor(model).run(_request())

    payload = json.loads(model.messages[0][1]["content"].rsplit("\n", 1)[-1])
    groups = payload["compact_evidence"]["groups"]
    assert "evidence_registry" not in payload
    assert [group["handle"] for group in groups] == ["E001", "E002"]
    assert result.trace.final_status == "accepted"
    assert result.trace.final_parsed_payload["claims"][0]["evidence_refs"] == [
        "E002"
    ]
    assert result.diagnosis.claims[0].evidence_refs == [
        result.trace.evidence_handle_map["E002"]
    ]
    assert all(
        "handle" not in item.observation.value for item in result.evidence_registry
    )
    assert result.evidence_registry[0].provenance[0].result_path == [
        "compact_evidence",
        "groups",
        0,
    ]


def test_lean_tool_result_maps_handle_to_existing_observation_once():
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
    result = _executor(model).run(_request())

    payload = _tool_payload(model.messages[-1][-1])
    assert "_system_evidence_items" not in payload
    assert payload["_evidence_handles"] == [
        {"handle": "E003", "path": ["events", 0]}
    ]
    visible_event = _path_value(payload, payload["_evidence_handles"][0]["path"])
    canonical_id = result.trace.evidence_handle_map["E003"]
    canonical = next(
        item for item in result.evidence_registry if item.evidence_id == canonical_id
    )
    assert visible_event == canonical.observation.value
    original_result = {
        key: value for key, value in payload.items() if key != "_evidence_handles"
    }
    assert json.dumps(payload).count("readiness deadline exceeded") == json.dumps(
        original_result
    ).count("readiness deadline exceeded")
    assert result.diagnosis.claims[0].evidence_refs == [canonical_id]


def test_lean_repeated_evidence_reuses_handle_without_renumbering():
    model = FakeModel(
        [
            FakeResponse(
                tool_calls=[
                    {"id": "call-1", "name": "get_events", "args": {"indices": [1]}}
                ]
            ),
            FakeResponse(
                tool_calls=[
                    {"id": "call-2", "name": "get_events", "args": {"indices": [1]}}
                ]
            ),
            FakeResponse(content=json.dumps(_diagnosis("E003"))),
        ]
    )
    result = _executor(model).run(_request())

    final_messages = model.messages[-1]
    first = _tool_payload(final_messages[3])
    second = _tool_payload(final_messages[5])
    assert first["_evidence_handles"] == second["_evidence_handles"] == [
        {"handle": "E003", "path": ["events", 0]}
    ]
    assert list(result.trace.evidence_handle_map) == ["E001", "E002", "E003"]
    item = next(
        item
        for item in result.evidence_registry
        if item.evidence_id == result.trace.evidence_handle_map["E003"]
    )
    assert [source.tool_call_id for source in item.provenance] == ["call-1", "call-2"]


def test_lean_runtime_handles_identify_entity_and_field_paths_without_leaks():
    runtime_state = {
        "services": {
            "runtime_service": {"state": "starting", "ready": False},
            "unqueried_service": {"state": "running", "ready": True},
        }
    }
    executor = _executor(None, runtime_state=runtime_state)
    executor._request_prompt(_request())
    call = {
        "id": "inspect-service",
        "name": "inspect_service_state_by_ref",
        "args": {
            "entity_ref": {
                "entity_type": "service",
                "entity_id": "service:runtime_service",
                "canonical_name": "runtime_service",
            }
        },
    }

    _, result = executor._execute_call(call)
    visible = executor._prepare_tool_result(call, result)
    mappings = visible["_evidence_handles"]

    assert "unqueried_service" not in json.dumps(visible)
    assert {tuple(mapping["path"]) for mapping in mappings} == {
        ("service",),
        ("state",),
        ("ready",),
    }
    assert [_path_value(visible, mapping["path"]) for mapping in mappings] == [
        "runtime_service",
        "starting",
        False,
    ]
    registry = {
        item.evidence_id: item for item in executor.evidence_registry.snapshot()
    }
    handles = executor.evidence_registry.handle_map()
    entity = registry[handles[mappings[0]["handle"]]]
    state = registry[handles[mappings[1]["handle"]]]
    assert entity.source_type == "entity" and entity.observation is None
    assert state.source_type == "runtime_state"
    assert state.observation.field == "state"


def test_lean_not_collected_is_visible_and_tool_errors_register_nothing():
    runtime_state = {
        "processes": {"worker": {"pid": 10, "state": "running"}},
    }
    executor = _executor(None, runtime_state=runtime_state)
    executor._request_prompt(_request())
    process_call = {
        "id": "inspect-process",
        "name": "inspect_process_state_by_ref",
        "args": {
            "entity_ref": {
                "entity_type": "process",
                "entity_id": "process:worker",
                "canonical_name": "worker",
            }
        },
    }
    _, process_result = executor._execute_call(process_call)
    visible = executor._prepare_tool_result(process_call, process_result)
    status_mapping = next(
        mapping
        for mapping in visible["_evidence_handles"]
        if mapping["path"] == ["cgroup_reference_status"]
    )
    assert _path_value(visible, status_mapping["path"]) == "not_collected"

    item_count = len(executor.evidence_registry.snapshot())
    error_call = {
        "id": "unknown-service",
        "name": "inspect_service_state_by_ref",
        "args": {
            "entity_ref": {
                "entity_type": "service",
                "entity_id": "service:missing",
                "canonical_name": "missing",
            }
        },
    }
    _, error_result = executor._execute_call(error_call)
    error_visible = executor._prepare_tool_result(error_call, error_result)
    assert error_visible == error_result
    assert "_evidence_handles" not in error_visible
    assert len(executor.evidence_registry.snapshot()) == item_count


def test_lean_finalization_keeps_turn_and_token_accounting():
    model = FakeModel(
        [
            FakeResponse(content="I am ready to summarize.", input_tokens=10, output_tokens=4),
            FakeResponse(
                content=json.dumps(_diagnosis("E002")),
                input_tokens=20,
                output_tokens=8,
            ),
        ]
    )
    result = _executor(model).run(_request())

    assert result.trace.final_status == "accepted"
    assert result.trace.finalization_fallback_triggered is True
    assert result.trace.model_turns == 2
    assert result.trace.tool_calling_rounds == 0
    assert result.trace.tool_calls == 0
    assert result.trace.input_tokens == 30
    assert result.trace.output_tokens == 12


def test_lean_view_reduces_bytes_without_changing_canonical_registry():
    baseline = RegistryBackedCompactDirectExecutor(
        None, _events(), reliability_hardening=True
    )
    lean = _executor(None)
    baseline_prompt = baseline._request_prompt(_request())
    lean_prompt = lean._request_prompt(_request())
    call = {"id": "call-1", "name": "get_events", "args": {"indices": [0, 1]}}

    _, baseline_result = baseline._execute_call(call)
    _, lean_result = lean._execute_call(call)
    baseline_visible = baseline._prepare_tool_result(call, baseline_result)
    lean_visible = lean._prepare_tool_result(call, lean_result)

    assert len(lean_prompt.encode("utf-8")) < len(baseline_prompt.encode("utf-8"))
    assert len(json.dumps(lean_visible).encode("utf-8")) < len(
        json.dumps(baseline_visible).encode("utf-8")
    )
    assert lean.evidence_registry.handle_map() == baseline.evidence_registry.handle_map()
    assert lean.evidence_registry.snapshot() == baseline.evidence_registry.snapshot()


def test_lean_view_requires_reliability_hardening():
    with pytest.raises(
        ValueError, match="deduplicated_evidence_view requires reliability_hardening"
    ):
        RegistryBackedCompactDirectExecutor(
            None, _events(), deduplicated_evidence_view=True
        )
