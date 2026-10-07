from types import SimpleNamespace

from runtime.ptc.runner import _evidence_boundary_audit, _tool_trace


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

    programs, tool_names, execute_results, agent_calls = _tool_trace(messages)

    assert tool_names == ["execute_code", "read_file"]
    assert len(programs) == 1
    assert execute_results == [
        {"bytes": 10, "success": True, "preview": "SUCCESS\nok"}
    ]
    assert agent_calls[1] == {
        "tool": "read_file",
        "arguments": {"file_path": "/home/daytona/code/analysis.py"},
    }


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
