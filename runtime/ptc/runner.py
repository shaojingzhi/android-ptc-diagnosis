"""Run the pinned external PTC agent against frozen replay snapshots."""

from __future__ import annotations

import argparse
import ast
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from android_ptc_diag.backend import ReplayBackend  # noqa: E402
from android_ptc_diag.cases import load_case  # noqa: E402
from android_ptc_diag.clustering import cluster_events  # noqa: E402
from android_ptc_diag.execution import (  # noqa: E402
    DIRECT_SYSTEM_PROMPT,
    _parse_diagnosis,
    _usage,
    _validate_diagnosis_indices,
)
from android_ptc_diag.models import LogEvent  # noqa: E402

UPSTREAM_REVISION = "425b9575bfdcaf949b0a4053e981cf8070c70e03"
PTC_RUNTIME_ROOT = Path(__file__).resolve().parent
DIAGNOSTIC_MCP = PTC_RUNTIME_ROOT / "diagnostic_mcp.py"
PRIMITIVE_NAMES = {
    "get_events",
    "filter_by_time",
    "filter_by_level",
    "filter_by_text",
    "filter_by_tag",
    "group_by_template",
    "first_occurrence",
    "group_by_pid",
}
ALLOWED_AGENT_TOOLS = {"execute_code"}
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]+")


def _model() -> Any:
    from langchain_openai import ChatOpenAI

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    return ChatOpenAI(
        model="deepseek-v4-pro",
        api_key=api_key,
        base_url="https://api.deepseek.com",
        temperature=0,
        extra_body={"thinking": {"type": "disabled"}},
    )


def _snapshot(events: list[LogEvent], request: Any) -> str:
    payload = {
        "events": [event.model_dump(mode="json") for event in events],
        "request": request.model_dump(mode="json"),
    }
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _message_type(message: Any) -> str | None:
    return getattr(message, "type", None)


def _call_name(call: dict[str, Any]) -> str:
    return str(call.get("name") or "unknown")


def _static_program_calls(code: str) -> list[dict[str, Any]]:
    """List primitive call sites in generated source; this is not runtime usage."""
    calls: list[dict[str, Any]] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return calls
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        else:
            continue
        if name not in PRIMITIVE_NAMES:
            continue
        arguments: dict[str, Any] = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                continue
            try:
                arguments[keyword.arg] = ast.literal_eval(keyword.value)
            except (ValueError, TypeError, SyntaxError):
                arguments[keyword.arg] = "<dynamic>"
        calls.append({"primitive": name, "arguments": arguments})
    return calls


def _redact(value: str, limit: int = 1200) -> str:
    return SECRET_PATTERN.sub("<redacted>", value)[:limit]


def _tool_trace(messages: list[Any]) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    programs: list[dict[str, Any]] = []
    tool_names: list[str] = []
    execute_results: list[dict[str, Any]] = []
    for message in messages:
        for call in getattr(message, "tool_calls", []) or []:
            name = _call_name(call)
            tool_names.append(name)
            if name != "execute_code":
                continue
            code = (call.get("args") or {}).get("code", "")
            programs.append(
                {
                    "sha256": hashlib.sha256(code.encode()).hexdigest(),
                    "length": len(code),
                    "text": _redact(code),
                    "static_primitive_calls": _static_program_calls(code),
                }
            )
        if _message_type(message) != "tool":
            continue
        content = str(getattr(message, "content", ""))
        execute_results.append(
            {
                "bytes": len(content.encode("utf-8")),
                "success": "SUCCESS" in content and "ERROR" not in content,
                "preview": _redact(content),
            }
        )
    return programs, tool_names, execute_results


def _diagnosis(messages: list[Any], event_count: int) -> tuple[dict[str, Any] | None, str | None]:
    ai_messages = [message for message in messages if _message_type(message) == "ai"]
    if not ai_messages:
        return None, "agent returned no AI message"
    final = ai_messages[-1]
    if getattr(final, "tool_calls", None):
        return None, "agent ended while requesting another tool"
    try:
        diagnosis = _parse_diagnosis(final)
        _validate_diagnosis_indices(diagnosis, event_count)
    except Exception as exc:  # noqa: BLE001 - trace the external runtime failure
        return None, str(exc)
    return diagnosis.model_dump(), None


def _model_usage(messages: list[Any]) -> tuple[int | None, int | None]:
    input_tokens = output_tokens = 0
    found_input = found_output = False
    for message in messages:
        current_input, current_output = _usage(message)
        if current_input is not None:
            input_tokens += current_input
            found_input = True
        if current_output is not None:
            output_tokens += current_output
            found_output = True
    return (input_tokens if found_input else None, output_tokens if found_output else None)


async def _upload_shared_modules(sandbox: Any, work_dir: str) -> None:
    await sandbox.sandbox.process.exec(f"mkdir -p {work_dir}/tools/android_ptc_diag")
    for name in ("__init__.py", "models.py", "primitives.py"):
        source = REPO_ROOT / "src" / "android_ptc_diag" / name
        if not await sandbox.aupload_file_bytes(
            f"{work_dir}/tools/android_ptc_diag/{name}", source.read_bytes()
        ):
            raise RuntimeError(f"failed to upload shared module: {name}")


async def run_case(case_dir: Path, trace_file: Path) -> dict[str, Any]:
    from ptc_agent import AgentConfig, PTCAgent
    from ptc_agent.config import MCPServerConfig
    from ptc_agent.core import SessionManager

    request, expected = load_case(case_dir)
    events = ReplayBackend(case_dir).collect_events(request)
    cluster_events(events)
    snapshot_b64 = _snapshot(events, request)
    server_config = MCPServerConfig(
        name="diagnostic",
        description="The eight deterministic normalized-log primitives.",
        command="uv",
        args=["run", "python", str(DIAGNOSTIC_MCP)],
        env={
            "PTC_SNAPSHOT_B64": snapshot_b64,
            "ANDROID_PTC_SRC": str(REPO_ROOT / "src"),
            "PTC_PRIMITIVE_TRACE_FILE": "/home/daytona/data/primitive-calls.jsonl",
        },
    )
    config = AgentConfig.create(
        llm=_model(),
        daytona_api_key=os.environ.get("DAYTONA_API_KEY"),
        daytona_base_url=os.environ.get("DAYTONA_BASE_URL", "https://app.daytona.io/api"),
        python_version="3.12",
        snapshot_enabled=False,
        snapshot_auto_create=False,
        mcp_servers=[server_config],
        subagents_enabled=[],
        skills_enabled=False,
        enable_view_image=False,
        background_auto_wait=False,
        use_custom_filesystem_tools=False,
        max_execution_time=180,
    )
    trace: dict[str, Any] = {
        "case_id": case_dir.name,
        "mode": "ptc",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "model_parameters": {"temperature": 0, "thinking": "disabled"},
        "upstream_revision": UPSTREAM_REVISION,
        "input_event_count": len(events),
        "model_turns": 0,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "execute_code_count": 0,
        "generated_programs": [],
        "static_primitive_calls": [],
        "execute_code_results": [],
        "primitive_calls": [],
        "sandbox_startup_ms": None,
        "execution_ms": None,
        "total_latency_ms": None,
        "final": None,
        "primary_candidate_indices": [],
        "evidence_indices": [],
        "propagation_event_indices": [],
        "top1": False,
        "top3": False,
        "errors": [],
        "forbidden_tool_use": [],
        "available_tools": [],
        "invalid": False,
    }
    session_id = f"m3b-{case_dir.name}-{int(time.time())}"
    total_started = time.perf_counter()
    session = None
    try:
        session = SessionManager.get_session(session_id, config.to_core_config())
        startup_started = time.perf_counter()
        original_cwd = Path.cwd()
        # Upstream discovers stdio tools locally before uploading the server.
        # Run that subprocess from this isolated project's dependency environment.
        os.chdir(PTC_RUNTIME_ROOT)
        try:
            await session.initialize()
        finally:
            os.chdir(original_cwd)
        assert session.sandbox is not None
        await _upload_shared_modules(
            session.sandbox,
            getattr(session.sandbox, "_work_dir", "/home/daytona"),
        )
        trace["sandbox_startup_ms"] = (time.perf_counter() - startup_started) * 1000
        ptc_agent = PTCAgent(config)
        agent = ptc_agent.create_agent(
            sandbox=session.sandbox,
            mcp_registry=session.mcp_registry,
            subagent_names=[],
            system_prompt_suffix=(
                DIRECT_SYSTEM_PROMPT
                + "\nUse execute_code and the diagnostic MCP primitives. Keep intermediate "
                "records inside the generated program and return only compact evidence."
            ),
        )
        trace["available_tools"] = list(getattr(ptc_agent, "native_tools", []))
        task = json.dumps(
            {
                "symptom": request.symptom,
                "target_package": request.target_package,
                "failure_timestamp": request.failure_timestamp.isoformat() if request.failure_timestamp else None,
                "event_count": len(events),
                "instructions": "Use execute_code to call only diagnostic primitives, then return the requested diagnosis JSON.",
                "output_schema": {
                    "primary_candidate_indices": "list[int]",
                    "evidence_indices": "list[int]",
                    "propagation_event_indices": "list[int]",
                    "notes": "list[str]",
                },
            },
            ensure_ascii=False,
        )
        execution_started = time.perf_counter()
        result = await agent.ainvoke({"messages": [("user", task)]})
        trace["execution_ms"] = (time.perf_counter() - execution_started) * 1000
        messages = result.get("messages", [])
        ai_messages = [message for message in messages if _message_type(message) == "ai"]
        trace["model_turns"] = len(ai_messages)
        trace["input_tokens"], trace["output_tokens"] = _model_usage(messages)
        if trace["input_tokens"] is not None and trace["output_tokens"] is not None:
            trace["total_tokens"] = trace["input_tokens"] + trace["output_tokens"]
        programs, tool_names, execute_results = _tool_trace(messages)
        trace["generated_programs"] = programs
        trace["execute_code_results"] = execute_results
        trace["execute_code_count"] = tool_names.count("execute_code")
        trace["forbidden_tool_use"] = sorted(set(tool_names) - ALLOWED_AGENT_TOOLS)
        trace["static_primitive_calls"] = [
            call
            for program in programs
            for call in program["static_primitive_calls"]
        ]
        runtime_trace_error: str | None = None
        try:
            runtime_trace_bytes = await session.sandbox.adownload_file_bytes(
                "/home/daytona/data/primitive-calls.jsonl"
            )
        except Exception as exc:  # noqa: BLE001 - preserve a clear trace error
            runtime_trace_bytes = None
            runtime_trace_error = f"runtime primitive trace missing: {exc}"
        if runtime_trace_bytes is None:
            trace["errors"].append(runtime_trace_error or "runtime primitive trace missing")
        else:
            try:
                runtime_calls: list[dict[str, Any]] = []
                for line in runtime_trace_bytes.decode("utf-8").splitlines():
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("trace record is not an object")
                    runtime_calls.append(value)
                trace["primitive_calls"] = runtime_calls
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                trace["errors"].append(f"malformed runtime primitive trace: {exc}")
        diagnosis, error = _diagnosis(messages, len(events))
        trace["final"] = diagnosis
        if trace["execute_code_count"] == 0:
            trace["errors"].append("no execute_code call was observed")
        if not trace["primitive_calls"]:
            trace["errors"].append("no diagnostic primitive call was observed")
        if any(not result["success"] for result in execute_results):
            trace["errors"].append("execute_code returned an error")
        if diagnosis:
            trace["primary_candidate_indices"] = diagnosis["primary_candidate_indices"]
            trace["evidence_indices"] = diagnosis["evidence_indices"]
            trace["propagation_event_indices"] = diagnosis["propagation_event_indices"]
            primary_messages = [
                events[index].message
                for index in diagnosis["primary_candidate_indices"]
                if 0 <= index < len(events)
            ]
            expected_text = expected["primary_event_contains"]
            trace["top1"] = bool(primary_messages and expected_text in primary_messages[0])
            trace["top3"] = any(expected_text in message for message in primary_messages[:3])
        if error:
            trace["errors"].append(error)
        if trace["forbidden_tool_use"]:
            trace["errors"].append(f"forbidden tool use: {trace['forbidden_tool_use']}")
        trace["invalid"] = bool(trace["errors"])
    except Exception as exc:  # noqa: BLE001 - preserve external integration failures in trace
        trace["errors"].append(str(exc))
        trace["invalid"] = True
    finally:
        trace["total_latency_ms"] = (time.perf_counter() - total_started) * 1000
        if session is not None:
            await SessionManager.cleanup_session(session_id)
    with trace_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(trace, ensure_ascii=False) + "\n")
    return trace


def _print_row(trace: dict[str, Any]) -> None:
    total = trace["total_tokens"]
    print(
        f"{trace['case_id']:<19} {trace.get('top1', 'n/a')!s:<5} {trace.get('top3', 'n/a')!s:<5} "
        f"{trace['model_turns']:>6} {trace['execute_code_count']:>7} {len(trace['primitive_calls']):>10} "
        f"{trace['input_tokens']!s:>7} {trace['output_tokens']!s:>7} {total!s:>7} "
        f"{trace['sandbox_startup_ms']!s:>12} {trace['total_latency_ms']!s:>12} {str(trace['invalid']):>7}"
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run pinned external PTC runtime")
    parser.add_argument("--case", help="one replay case; default: all three seed cases")
    parser.add_argument("--trace-file", type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get("DAYTONA_API_KEY"):
        raise SystemExit("DAYTONA_API_KEY is required for the real PTC smoke run")
    cases_root = REPO_ROOT / "eval" / "cases"
    if args.case:
        cases = [cases_root / args.case]
    else:
        cases = sorted(path for path in cases_root.iterdir() if path.is_dir())
    if any(not case.is_dir() for case in cases):
        raise SystemExit("unknown replay case")
    args.trace_file.parent.mkdir(parents=True, exist_ok=True)
    print("case                top1  top3  turns  execute  primitives  input  output  total  startup_ms  total_ms  invalid")
    for case in cases:
        trace = await run_case(case, args.trace_file)
        _print_row(trace)


if __name__ == "__main__":
    asyncio.run(main())
