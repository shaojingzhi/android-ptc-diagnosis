import importlib.util
import json
from pathlib import Path

from android_ptc_diag.models import TriageRequest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "eval_public_android_container_workflow.py"
SPEC = importlib.util.spec_from_file_location("public_container_workflow", SCRIPT)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class _FinalModel:
    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):
        prompt = json.loads(messages[1]["content"].rsplit("\n", 1)[-1])
        state = next(
            item
            for item in prompt["captured_runtime_evidence"]
            if (item.get("observation") or {}).get("field") == "state"
        )
        return {
            "content": json.dumps(
                {
                    "claims": [
                        {
                            "statement": "The captured container state was running.",
                            "type": "observation",
                            "evidence_refs": [state["handle"]],
                        }
                    ],
                    "evidence_gaps": [],
                    "recommended_next_checks": [],
                }
            )
        }


def test_public_workflow_keeps_real_artifacts_separate_and_model_visible():
    prepared = RUNNER.prepare_evaluation(ROOT)
    prompt = prepared["task_prompt"]
    payload = json.loads(prompt.rsplit("\n", 1)[-1])

    assert len(prepared["events"]) == 75
    assert len(payload["compact_evidence"]["groups"]) == 45
    assert len(payload["captured_runtime_evidence"]) == 3
    assert len(payload["captured_runtime_evidence"][2]["observation"]["value"]) == 82
    assert payload["captured_state_semantics"].endswith(
        "do not infer a strict order between them and Logcat."
    )
    assert "redroid_boot_health_001" not in prompt
    assert "assessment.yaml" not in prompt
    assert "root_cause" not in prompt
    assert all(
        item.provenance[0].origin == "initial_artifact"
        for item in prepared["evidence_registry"][-3:]
    )


def test_public_workflow_mock_final_can_cite_captured_state():
    prepared = RUNNER.prepare_evaluation(ROOT)
    executor = RUNNER.PublicIncidentExecutor(
        _FinalModel(),
        prepared["events"],
        captured_artifacts=prepared["captured_artifacts"],
    )
    result = executor.run(TriageRequest(symptom="neutral public incident"))

    assert result.trace.final_status == "accepted"
    assert result.diagnosis is not None
    cited = result.diagnosis.claims[0].evidence_refs[0]
    registry = {item.evidence_id: item for item in result.evidence_registry}
    assert cited in registry
    assert registry[cited].subject.id == "container:runtime-under-review"
    assert registry[cited].observation.value["Running"] is True
    assert not _FinalModel().bind_tools([]).tools


def test_waydroid_workflow_is_log_only_and_keeps_hidden_answer_out_of_prompt():
    prepared = RUNNER.prepare_evaluation(ROOT, "waydroid_hwcomposer_1894")
    prompt = prepared["task_prompt"]
    payload = json.loads(prompt.rsplit("\n", 1)[-1])

    assert len(prepared["events"]) == 157
    assert len(payload["compact_evidence"]["groups"]) == 73
    assert prepared["captured_artifacts"] == []
    assert "captured_runtime_evidence" not in payload
    assert "waydroid_hwcomposer_1894" not in prompt
    assert "issuecomment" not in prompt
    assert "3b2f29d" not in prompt
    assert "missing wayland pointer" not in prompt.lower()
    assert "root_cause" not in prompt
