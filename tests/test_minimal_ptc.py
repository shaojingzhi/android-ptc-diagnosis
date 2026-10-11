import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from android_ptc_diag.backend import ReplayBackend
from android_ptc_diag.cases import load_case
from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.cases import load_runtime_state
from android_ptc_diag.diagnostic_loop import run_semantic_diagnosis_loop
from android_ptc_diag.models import DirectDiagnosis
from runtime.ptc import minimal_runner
from runtime.ptc.skill_compact_runner import _build_skill_compact_task


CASE_DIR = Path(__file__).parents[1] / "eval" / "cases" / "binder_flood_001"
RUNTIME_CASE_DIR = Path(__file__).parents[1] / "eval" / "cases" / "runtime_mount_failure_001"
PROCESS_CASE_DIR = Path(__file__).parents[1] / "eval" / "cases" / "runtime_process_killed_001"


class _Schema:
    @staticmethod
    def model_json_schema():
        return {
            "title": "execute_code",
            "description": "duplicate tool description",
            "type": "object",
            "properties": {"code": {"title": "Code", "type": "string"}},
            "required": ["code"],
        }


class _ExecuteTool:
    name = "execute_code"
    description = "Execute Python code in the sandbox."

    def get_input_schema(self):
        return _Schema

    async def ainvoke(self, arguments):
        assert set(arguments) == {"code"}
        return "SUCCESS\ncompact evidence"


class _Model:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages):
        assert messages[0]["role"] == "system"
        return self.responses.pop(0)


class _DecisionModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    def invoke(self, messages):
        self.messages.append(messages)
        return next(self.responses)


def _response(content, tool_calls, input_tokens, output_tokens):
    return SimpleNamespace(
        type="ai",
        content=content,
        tool_calls=tool_calls,
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )


def test_minimal_prompt_exposes_only_bounded_api_contract():
    prompt = minimal_runner.MINIMAL_SYSTEM_PROMPT

    for name in minimal_runner.runner.PRIMITIVE_NAMES | {"inspect_group_evidence"}:
        assert name in prompt
    assert "select_group_evidence_indices" not in prompt
    assert "build_timeline" not in prompt
    assert "group_ids" in prompt
    assert "recreate" in prompt
    assert "group dictionaries" in prompt
    assert "evidence_view" in prompt
    assert "cross_source_order=\"unknown\"" in prompt
    assert "uncertainties" in prompt
    assert "recommended_next_checks" in prompt
    assert "proven root cause" in prompt
    assert "root_cause" not in prompt
    assert "Do not read files" in prompt
    assert "diagnose_root_cause" not in prompt
    source = inspect.getsource(minimal_runner)
    assert "PTCAgent" not in source
    assert "from deepagents" not in source
    assert "create_deep_agent" not in source


def test_semantic_decision_prompt_exposes_structured_followup_contract_without_tools():
    prompt = minimal_runner.SEMANTIC_DECISION_PROMPT

    for field in ("diagnosis", "followup_request", "uncertainty", "check", "arguments", "reason"):
        assert field in prompt
    for check in (
        "inspect_process_state",
        "inspect_cgroup_state",
        "inspect_service_state",
        "inspect_mount_state",
    ):
        assert check in prompt
    assert "never remediation" in prompt
    assert 'optional string "name"' in prompt
    assert 'integer "pid"' in prompt


def test_model_loop_binds_only_execute_code_and_records_full_program():
    code = (
        "from tools.android_ptc_diag.skills import build_timeline\n"
        "from tools.diagnostic import get_events\n"
        "events = get_events(indices=[1])['events']\n"
        "print(build_timeline(events))\n"
        + "# retained instrumentation text\n" * 50
    )
    final = json.dumps(
        {
            "primary_candidate_indices": [1],
            "evidence_indices": [1],
            "propagation_event_indices": [],
            "notes": ["candidate"],
        }
    )
    model = _Model(
        [
            _response(
                "",
                [{"id": "call-1", "name": "execute_code", "args": {"code": code}}],
                100,
                20,
            ),
            _response(final, [], 150, 30),
        ]
    )
    result = asyncio.run(
        minimal_runner._model_loop(model, _ExecuteTool(), "compact task")
    )

    assert [tool.name for tool in model.bound_tools] == ["execute_code"]
    assert len(result["turns"]) == 2
    assert result["turns"][0]["input_tokens"] == 100
    assert result["turns"][1]["input_tokens"] == 150
    assert result["turns"][1]["context_chars"] > result["turns"][0]["context_chars"]
    assert result["generated_programs"][0]["text"] == code
    assert len(result["generated_programs"][0]["text"]) > 1200
    assert result["generated_programs"][0]["static_skill_calls"] == ["build_timeline"]
    assert result["generated_programs"][0]["static_primitive_calls"] == [
        {"primitive": "get_events", "arguments": {"indices": [1]}}
    ]
    assert result["execute_code_results"][0]["success"] is True
    assert result["errors"] == []


def test_mocked_model_backed_semantic_loop_updates_diagnosis_without_tools():
    initial = DirectDiagnosis(
        primary_candidate_indices=[1],
        evidence_indices=[1],
        uncertainties=["why the runtime service process died is not established"],
    )
    updated = initial.model_copy(deep=True)
    updated.evidence_indices = [1, 2]
    updated.uncertainties = []
    request = {
        "uncertainty": "why the runtime service process died is not established",
        "check": "inspect_process_state",
        "arguments": {"name": "runtime_service"},
        "reason": "Check whether the runtime process is dead.",
    }
    model = _DecisionModel(
        [
            _response(
                json.dumps({"diagnosis": initial.model_dump(), "followup_request": request}),
                [],
                31,
                17,
            ),
            _response(
                json.dumps({"diagnosis": updated.model_dump(), "followup_request": None}),
                [],
                43,
                21,
            ),
        ]
    )
    provider = minimal_runner.ModelDecisionProvider(model, event_count=3)

    result = run_semantic_diagnosis_loop(
        initial,
        load_runtime_state(PROCESS_CASE_DIR),
        provider,
    )

    assert len(model.messages) == 2
    assert result.current_diagnosis.evidence_indices == [1, 2]
    assert result.current_diagnosis.uncertainties == []
    assert result.stop_reason == "model chose to stop"
    assert len(provider.turns) == 2
    assert provider.turns[1]["returned_evidence"]["state"] == "dead"
    assert provider.turns[1]["stop_reason"] == "model chose to stop"
    assert provider.turns[0]["input_tokens"] == 31
    assert provider.turns[1]["output_tokens"] == 21
    assert "followup_request" not in result.current_diagnosis.model_dump()


def test_model_turn_cap_does_not_execute_an_unreadable_last_result():
    calls = [
        _response(
            "",
            [{"id": f"call-{index}", "name": "execute_code", "args": {"code": "print(1)"}}],
            10,
            1,
        )
        for index in range(minimal_runner.MAX_MODEL_TURNS)
    ]

    result = asyncio.run(
        minimal_runner._model_loop(_Model(calls), _ExecuteTool(), "compact task")
    )

    assert len(result["turns"]) == minimal_runner.MAX_MODEL_TURNS
    assert len(result["execute_code_results"]) == minimal_runner.MAX_MODEL_TURNS - 1
    assert result["errors"] == ["model requested tools after MAX_MODEL_TURNS"]


def test_fixed_context_measurement_is_well_below_deepagents_baseline():
    request, _ = load_case(CASE_DIR)
    events = ReplayBackend(CASE_DIR).collect_events(request)
    cluster_events(events)
    task = _build_skill_compact_task(request, events)

    measurement = minimal_runner.fixed_context_measurements(task, _ExecuteTool())

    assert measurement["first_turn_total"]["estimated_tokens"] < 4_000
    assert measurement["deepagents_first_turn_estimated_tokens"] == 8_884
    assert measurement["execute_code_schema"]["chars"] > 0
    assert "duplicate tool description" not in json.dumps(
        minimal_runner._tool_schema(_ExecuteTool())
    )


def test_minimal_task_wires_diagnosis_scope_and_output_contract():
    request, _ = load_case(RUNTIME_CASE_DIR)
    events = ReplayBackend(RUNTIME_CASE_DIR).collect_events(request)
    task = json.loads(_build_skill_compact_task(request, events))

    assert task["diagnosis_scope"] == "container_runtime_boundary"
    assert set(task["output_schema"]) == {
        "primary_candidate_indices",
        "evidence_indices",
        "propagation_event_indices",
        "uncertainties",
        "recommended_next_checks",
        "notes",
    }
    assert "root_cause" not in task["output_schema"]


def test_older_case_without_scope_remains_compatible():
    request, _ = load_case(CASE_DIR)
    events = ReplayBackend(CASE_DIR).collect_events(request)
    task = json.loads(_build_skill_compact_task(request, events))

    assert request.diagnosis_scope is None
    assert task["diagnosis_scope"] is None
    assert "root_cause" not in task["output_schema"]
