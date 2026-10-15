import json

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


class BoundFakeModel:
    def __init__(self, owner):
        self.owner = owner

    def invoke(self, messages):
        return self.owner.invoke_as("bound", messages)


class FinalizationAwareFakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.invocation_modes = []
        self.messages = []
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return BoundFakeModel(self)

    def invoke(self, messages):
        return self.invoke_as("unbound", messages)

    def invoke_as(self, mode, messages):
        self.invocation_modes.append(mode)
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


def _run(responses):
    model = FinalizationAwareFakeModel(responses)
    result = RegistryBackedCompactDirectExecutor(
        model, _events(), reliability_hardening=True
    ).run(_request())
    return model, result


def test_case03_like_prose_triggers_one_tool_free_finalization():
    final = json.dumps(_diagnosis("E002"))
    model, result = _run(
        [
            FakeResponse(
                content=(
                    "I have the full event sequence. Let me check the runtime entities "
                    "to understand the processes involved."
                ),
                input_tokens=10,
                output_tokens=4,
            ),
            FakeResponse(content=final, input_tokens=20, output_tokens=8),
        ]
    )

    trace = result.trace
    assert trace.final_status == "accepted"
    assert trace.finalization_fallback_triggered is True
    assert trace.finalization_trigger_status == "json_parse_failed"
    assert "not JSON" in trace.finalization_trigger_error
    assert "not JSON" in trace.errors[0]
    assert trace.initial_non_json_response.startswith("I have the full")
    assert trace.finalization_response == final
    assert trace.final_raw_text == final
    assert trace.finalization_model_turn == 2
    assert trace.model_turns == 2
    assert trace.input_tokens == 30
    assert trace.output_tokens == 12
    assert trace.tool_calling_rounds == 0
    assert trace.tool_calls == 0
    assert model.invocation_modes == ["bound", "unbound"]
    assert model.messages[-1][-1]["role"] == "user"
    assert "Do not call tools" in model.messages[-1][-1]["content"]
    assert trace.model_call_details[-1]["phase"] == "finalization"
    assert trace.total_latency_ms >= sum(
        turn["latency_ms"] for turn in trace.model_call_details
    )


def test_finalization_prose_fails_once_and_preserves_redacted_responses():
    model, result = _run(
        [
            FakeResponse(content="Inspect another entity. sk-original-secret"),
            FakeResponse(content="I still need evidence. sk-final-secret"),
        ]
    )

    trace = result.trace
    assert result.diagnosis is None
    assert trace.final_status == "json_parse_failed"
    assert trace.finalization_fallback_triggered is True
    assert trace.model_turns == 2
    assert trace.stop_reason == "finalization fallback failed: json_parse_failed"
    assert trace.initial_non_json_response.endswith("<redacted>")
    assert trace.finalization_response.endswith("<redacted>")
    assert model.invocation_modes == ["bound", "unbound"]


def test_finalization_invalid_schema_is_rejected():
    _, result = _run(
        [
            FakeResponse(content="I will now summarize."),
            FakeResponse(content=json.dumps({"claims": []})),
        ]
    )

    assert result.diagnosis is None
    assert result.trace.final_status == "schema_validation_failed"
    assert result.trace.stop_reason == (
        "finalization fallback failed: schema_validation_failed"
    )
    assert result.trace.final_validation_errors[0].stage == "schema_validation"


def test_case06_like_unknown_handle_is_rejected_at_exact_claim_path():
    payload = _diagnosis("E001")
    payload["claims"] = [
        {
            "statement": f"Observation {index}",
            "type": "observation",
            "evidence_refs": ["E001" if index < 6 else "E999"],
        }
        for index in range(7)
    ]
    model, result = _run(
        [
            FakeResponse(content="The investigation is complete."),
            FakeResponse(content=json.dumps(payload)),
        ]
    )

    assert result.diagnosis is None
    assert result.trace.final_status == "reference_validation_failed"
    issue = result.trace.final_validation_errors[0]
    assert issue.path == ["claims", 6, "evidence_refs", 0]
    assert issue.message == "unknown evidence handle: E999"
    assert result.trace.final_parsed_payload["claims"][6]["evidence_refs"] == [
        "E999"
    ]
    assert result.trace.final_resolved_payload is None
    assert model.invocation_modes == ["bound", "unbound"]


def test_finalization_tool_request_is_not_executed():
    _, result = _run(
        [
            FakeResponse(content="I need another check."),
            FakeResponse(
                tool_calls=[
                    {"id": "forbidden", "name": "get_events", "args": {"indices": [1]}}
                ]
            ),
        ]
    )

    assert result.diagnosis is None
    assert result.trace.tool_calls == 0
    assert result.trace.tool_calling_rounds == 0
    assert result.trace.final_status == "not_returned"
    assert result.trace.stop_reason == (
        "finalization response requested tools; diagnostic tools are disabled"
    )


def test_hardened_executor_keeps_six_tool_round_limit_without_finalization():
    responses = [
        FakeResponse(
            tool_calls=[
                {"id": f"call-{index}", "name": "get_events", "args": {"indices": [1]}}
            ]
        )
        for index in range(7)
    ]
    model, result = _run(responses)

    assert result.diagnosis is None
    assert result.trace.model_turns == 7
    assert result.trace.tool_calling_rounds == 6
    assert result.trace.tool_calls == 6
    assert result.trace.stopped_by_round_limit is True
    assert result.trace.finalization_fallback_triggered is False
    assert model.invocation_modes == ["bound"] * 7
