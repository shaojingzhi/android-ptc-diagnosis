import json

from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.execution import DirectExecutor
from android_ptc_diag.models import LogEvent, TriageRequest


class FakeResponse:
    def __init__(self, *, content: str = "", tool_calls: list[dict] | None = None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeModel:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = iter(responses)
        self.bound_tools = None
        self.messages = []

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, messages):
        self.messages.append(messages)
        return next(self.responses)


def _events() -> list[LogEvent]:
    return [
        LogEvent(source="replay", level="I", pid=7, tag="Demo", message="started"),
        LogEvent(source="replay", level="E", pid=7, tag="AndroidRuntime", message="FATAL EXCEPTION: main"),
        LogEvent(source="replay", level="E", pid=7, tag="AndroidRuntime", message="Process has died"),
    ]


def _request() -> TriageRequest:
    return TriageRequest(symptom="app crash", target_package="com.example.demo")


def _final(indices: list[int]) -> FakeResponse:
    return FakeResponse(
        content=json.dumps(
            {
                "primary_candidate_indices": indices,
                "evidence_indices": indices,
                "propagation_event_indices": [2],
                "notes": ["candidate only"],
            }
        )
    )


def test_direct_loop_returns_structured_final_after_tool_result():
    model = FakeModel(
        [
            FakeResponse(
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "filter_by_level",
                        "args": {"levels": ["E"]},
                    }
                ]
            ),
            _final([1]),
        ]
    )
    events = _events()
    cluster_events(events)

    result = DirectExecutor(model, events).run(
        _request(), case_id="direct-test", model_metadata={"model": "fake", "provider": "test"}
    )

    assert result.diagnosis is not None
    assert result.diagnosis.primary_candidate_indices == [1]
    assert result.trace.model_turns == 2
    assert result.trace.tool_calls == 1
    assert result.trace.tool_call_details[0]["record_count"] == 2
    assert result.trace.input_event_count == 3
    assert result.trace.case_id == "direct-test"
    assert result.trace.stopped_by_round_limit is False
    assert result.trace.model_dump()["mode"] == "direct"
    assert result.trace.error is None
    assert model.bound_tools and len(model.bound_tools) == 8


def test_direct_loop_supports_multiple_tool_rounds():
    model = FakeModel(
        [
            FakeResponse(
                tool_calls=[
                    {"id": "call-1", "name": "filter_by_text", "args": {"text": "fatal"}}
                ]
            ),
            FakeResponse(
                tool_calls=[
                    {"id": "call-2", "name": "get_events", "args": {"indices": [1]}}
                ]
            ),
            _final([1]),
        ]
    )

    result = DirectExecutor(model, _events()).run(_request())

    assert result.diagnosis is not None
    assert result.trace.model_turns == 3
    assert result.trace.tool_calls == 2
    assert [item["tool"] for item in result.trace.tool_call_details] == [
        "filter_by_text",
        "get_events",
    ]


def test_direct_loop_stops_at_round_limit():
    responses = [
        FakeResponse(
            tool_calls=[
                {"id": f"call-{index}", "name": "filter_by_level", "args": {"levels": ["E"]}}
            ]
        )
        for index in range(6)
    ]

    result = DirectExecutor(FakeModel(responses), _events()).run(_request())

    assert result.diagnosis is None
    assert result.trace.model_turns == 6
    assert result.trace.tool_calls == 6
    assert result.trace.stopped_by_round_limit is True
    assert result.trace.error == "maximum tool rounds reached: 6"
    assert any("maximum tool rounds" in error for error in result.trace.errors)


def test_unknown_tool_is_returned_as_clear_tool_error():
    model = FakeModel(
        [
            FakeResponse(tool_calls=[{"id": "call-1", "name": "not_a_primitive", "args": {}}]),
            _final([]),
        ]
    )

    result = DirectExecutor(model, _events()).run(_request())

    assert result.diagnosis is not None
    assert result.trace.tool_calls == 1
    assert result.trace.error == "unknown diagnostic primitive: not_a_primitive"
    assert "unknown diagnostic primitive" in result.trace.errors[0]
    assert result.trace.tool_call_details[0]["error"]
