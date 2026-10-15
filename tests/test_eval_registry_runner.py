import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "eval_registry_backed_direct.py"
SPEC = importlib.util.spec_from_file_location("eval_registry_backed_direct", SCRIPT)
assert SPEC and SPEC.loader
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)

CASES = [
    f"startup_{mechanism}_{version}"
    for mechanism in (
        "mount_config",
        "cgroup_oom",
        "dependency_unready",
        "userns_limit",
    )
    for version in ("full", "limited")
]


@pytest.mark.parametrize("case_id", CASES)
def test_registry_eval_prepares_case_without_evaluation_metadata(case_id):
    prepared = RUNNER.prepare_evaluation(ROOT, case_id)
    prompt = prepared["task_prompt"]

    assert prepared["events"]
    assert len(prepared["tool_schema"]) == 9
    assert case_id not in prompt
    assert "pair_id" not in prompt
    assert "evidence_version" not in prompt
    assert "model_incident_id" not in prompt
    assert "hand_authored_synthetic" not in prompt
    assert "public_case_adapted" not in prompt
    assert "allowed_conclusion" not in prompt
    assert "unsupported_claims" not in prompt
    assert "startup-eval-neutral" not in prompt


def test_registry_eval_rejects_unknown_case_path():
    with pytest.raises(ValueError, match="unknown replay case"):
        RUNNER.prepare_evaluation(ROOT, "../outside")
