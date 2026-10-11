import asyncio
import json
from pathlib import Path

from android_ptc_diag.backend import ReplayBackend
from android_ptc_diag.cases import load_case
from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.compact import build_compact_evidence, compact_model_view
from runtime.ptc import skill_compact_runner
from runtime.ptc.runner import _shared_module_names


CASE_DIR = Path(__file__).parents[1] / "eval" / "cases" / "binder_flood_001"


def _task():
    request, _ = load_case(CASE_DIR)
    events = ReplayBackend(CASE_DIR).collect_events(request)
    cluster_events(events)
    task = json.loads(skill_compact_runner._build_skill_compact_task(request, events))
    return task, events


def test_task_reuses_frozen_compact_model_view():
    task, events = _task()

    assert task["compact_evidence"] == compact_model_view(
        build_compact_evidence(events)
    )
    assert task["raw_event_count"] == 500
    assert task["compact_group_count"] == 13


def test_task_excludes_provenance_and_ground_truth_hints():
    task, _ = _task()

    assert all("indices" not in group for group in task["compact_evidence"]["groups"])
    assert not {"expected", "ground_truth", "primary_index"} & set(task)
    authored_text = (
        task["instructions"] + skill_compact_runner.SKILL_COMPACT_PTC_SYSTEM_SUFFIX
    ).lower()
    assert "ground_truth" not in authored_text
    assert "expected root" not in authored_text
    assert "primary index 20" not in authored_text


def test_system_prompt_exposes_the_correct_skill_composition_contract():
    prompt = skill_compact_runner.SKILL_COMPACT_PTC_SYSTEM_SUFFIX

    assert "android_ptc_diag.skills" in prompt
    assert "from tools.android_ptc_diag.skills import" in prompt
    assert "inspect_group_evidence" in prompt
    assert "group_id" in prompt
    assert "evidence_view" in prompt
    assert "build_timeline(raw)" not in prompt


def test_wrapper_passes_only_the_skill_compact_variation(monkeypatch, tmp_path):
    captured = {}

    async def fake_run_case(case_dir, trace_file, **kwargs):
        captured.update(kwargs)
        return {"case_dir": case_dir, "trace_file": trace_file}

    monkeypatch.setattr(skill_compact_runner.runner, "_run_case", fake_run_case)
    result = asyncio.run(
        skill_compact_runner.run_case(CASE_DIR, tmp_path / "trace.jsonl")
    )

    assert result["case_dir"] == CASE_DIR
    assert captured == {
        "mode": "skill_compact_ptc",
        "task_builder": skill_compact_runner._build_skill_compact_task,
        "system_prompt_suffix": skill_compact_runner.SKILL_COMPACT_PTC_SYSTEM_SUFFIX,
        "extra_shared_modules": ("skills.py",),
    }


def test_skill_module_uses_the_existing_safe_upload_contract():
    assert _shared_module_names(("skills.py",)) == (
        "__init__.py",
        "models.py",
        "primitives.py",
        "skills.py",
    )
