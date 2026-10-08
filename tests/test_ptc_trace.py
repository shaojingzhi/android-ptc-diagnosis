import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from android_ptc_diag.backend import ReplayBackend
from android_ptc_diag.cases import load_case
from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.execution import DIRECT_SYSTEM_PROMPT
from runtime.ptc.runner import (
    DEFAULT_SHARED_MODULES,
    RAW_PTC_SYSTEM_SUFFIX,
    _build_raw_task,
    _evidence_boundary_audit,
    _finalize_validity,
    _run_case,
    _shared_module_names,
    _snapshot_bytes,
    _tool_trace,
    _upload_shared_modules,
)


CASES = Path(__file__).parents[1] / "eval" / "cases"


class _FakeProcess:
    async def exec(self, command):
        self.command = command


class _FakeSandbox:
    def __init__(self):
        self.sandbox = SimpleNamespace(process=_FakeProcess())
        self.uploaded = []

    async def aupload_file_bytes(self, target, content):
        self.uploaded.append((target, content))
        return True


def _validity_trace(results):
    return {
        "execute_code_results": results,
        "execute_code_count": len(results),
        "execute_code_successes": 0,
        "execute_code_failures": 0,
        "recovered_execution_error": False,
        "primitive_calls": [{"primitive": "get_events"}],
        "forbidden_tool_use": [],
        "evidence_bypass": False,
        "evidence_boundary_checks": {"reasons": []},
        "validation_errors": [],
        "execution_errors": [],
        "errors": [],
        "invalid": False,
    }


def test_large_ptc_snapshot_serialization_is_deterministic():
    case_dir = CASES / "binder_flood_001"
    request, _ = load_case(case_dir)
    events = ReplayBackend(case_dir).collect_events(request)
    cluster_events(events)

    first = _snapshot_bytes(events, request)
    second = _snapshot_bytes(events, request)

    assert first == second
    assert len(first) > 100_000
    assert len(json.loads(first)["events"]) == 500


def test_raw_ptc_defaults_remain_unchanged():
    request, _ = load_case(CASES / "java_crash_001")
    events = ReplayBackend(CASES / "java_crash_001").collect_events(request)
    task = json.loads(_build_raw_task(request, events))

    assert set(task) == {
        "symptom",
        "target_package",
        "failure_timestamp",
        "event_count",
        "instructions",
        "output_schema",
    }
    assert "compact" not in task
    assert task["instructions"] == (
        "Use execute_code to call only diagnostic primitives, then return the "
        "requested diagnosis JSON."
    )
    assert RAW_PTC_SYSTEM_SUFFIX == (
        DIRECT_SYSTEM_PROMPT
        + "\nUse execute_code and the diagnostic MCP primitives. Keep intermediate "
        "records inside the generated program and return only compact evidence."
    )
    assert inspect.signature(_run_case).parameters["mode"].default == "ptc"
    assert DEFAULT_SHARED_MODULES == ("__init__.py", "models.py", "primitives.py")
    assert _shared_module_names() == DEFAULT_SHARED_MODULES


def test_runner_hooks_express_future_skill_mode_without_a_sandbox():
    def marker_task(request, events):
        return "task marker"

    bound = inspect.signature(_run_case).bind(
        CASES / "binder_flood_001",
        Path("trace.jsonl"),
        mode="skill_compact_ptc",
        task_builder=marker_task,
        system_prompt_suffix="system marker",
        extra_shared_modules=("skills.py",),
    )

    assert bound.arguments["mode"] == "skill_compact_ptc"
    assert bound.arguments["task_builder"](None, []) == "task marker"
    assert bound.arguments["system_prompt_suffix"] == "system marker"
    assert _shared_module_names(bound.arguments["extra_shared_modules"]) == (
        "__init__.py",
        "models.py",
        "primitives.py",
        "skills.py",
    )


def test_shared_module_upload_defaults_and_extra_module_are_deterministic():
    default_sandbox = _FakeSandbox()
    asyncio.run(_upload_shared_modules(default_sandbox, "/work"))
    assert [Path(target).name for target, _ in default_sandbox.uploaded] == [
        "__init__.py",
        "models.py",
        "primitives.py",
    ]

    skill_sandbox = _FakeSandbox()
    asyncio.run(
        _upload_shared_modules(
            skill_sandbox, "/work", extra_modules=("skills.py",)
        )
    )
    assert [Path(target).name for target, _ in skill_sandbox.uploaded] == [
        "__init__.py",
        "models.py",
        "primitives.py",
        "skills.py",
    ]
    with pytest.raises(ValueError, match="duplicate"):
        _shared_module_names(("skills.py", "skills.py"))
    with pytest.raises(ValueError, match="filenames"):
        _shared_module_names(("../skills.py",))


def test_tool_trace_attributes_only_execute_code_results():
    messages = [
        SimpleNamespace(
            type="ai",
            tool_calls=[
                {"id": "call-code", "name": "execute_code", "args": {"code": "print('ok')"}},
                {
                    "id": "call-read",
                    "name": "read_file",
                    "args": {"file_path": "/home/daytona/code/analysis.py"},
                },
            ],
        ),
        SimpleNamespace(type="tool", tool_call_id="call-code", content="SUCCESS\nok"),
        SimpleNamespace(type="tool", tool_call_id="call-read", content="arbitrary content"),
    ]

    programs, tool_names, execute_results, agent_calls, attribution_errors = _tool_trace(
        messages
    )

    assert tool_names == ["execute_code", "read_file"]
    assert len(programs) == 1
    assert execute_results == [
        {"bytes": 10, "success": True, "preview": "SUCCESS\nok"}
    ]
    assert agent_calls[1] == {
        "tool": "read_file",
        "arguments": {"file_path": "/home/daytona/code/analysis.py"},
    }
    assert attribution_errors == []


def test_evidence_boundary_flags_only_evidence_bearing_filesystem_access():
    safe_calls = [
        {
            "tool": "write_file",
            "arguments": {"file_path": "/home/daytona/code/analysis.py"},
        }
    ]
    bypass_calls = [
        {
            "tool": "read_file",
            "arguments": {"file_path": "/home/daytona/data/primitive-calls.jsonl"},
        }
    ]

    assert _evidence_boundary_audit(safe_calls)[0] is False
    bypass, checks = _evidence_boundary_audit(bypass_calls)
    assert bypass is True
    assert checks["read_primitive_trace"] is True


def test_recovered_execute_failure_does_not_invalidate_run():
    trace = _validity_trace(
        [
            {"success": True, "preview": "SUCCESS"},
            {"success": False, "preview": "NameError"},
            {"success": True, "preview": "SUCCESS"},
        ]
    )

    _finalize_validity(trace, final_valid=True)

    assert trace["execute_code_successes"] == 2
    assert trace["execute_code_failures"] == 1
    assert trace["recovered_execution_error"] is True
    assert trace["execution_errors"] == ["execute_code[2] failed: NameError"]
    assert trace["validation_errors"] == []
    assert trace["invalid"] is False


def test_evidence_bypass_remains_a_validation_failure():
    trace = _validity_trace([{"success": True, "preview": "SUCCESS"}])
    trace["evidence_bypass"] = True
    trace["evidence_boundary_checks"] = {"reasons": ["read runtime primitive trace"]}

    _finalize_validity(trace, final_valid=True)

    assert trace["invalid"] is True
    assert trace["validation_errors"] == [
        "diagnostic evidence bypass: ['read runtime primitive trace']"
    ]
