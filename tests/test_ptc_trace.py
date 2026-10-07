from types import SimpleNamespace

from runtime.ptc.runner import _evidence_boundary_audit, _finalize_validity, _tool_trace


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
