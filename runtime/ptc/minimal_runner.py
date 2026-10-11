"""Run compact PTC with one model-visible tool and no DeepAgents agent harness."""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from android_ptc_diag.backend import ReplayBackend  # noqa: E402
from android_ptc_diag.cases import load_case, load_runtime_state  # noqa: E402
from android_ptc_diag.clustering import cluster_events  # noqa: E402
from android_ptc_diag.diagnostic_loop import run_semantic_diagnosis_loop  # noqa: E402
from android_ptc_diag.execution import _content, _usage, _validate_diagnosis_indices  # noqa: E402
from android_ptc_diag.models import DiagnosisLoopDecision, DirectDiagnosis  # noqa: E402
from runtime.ptc import runner  # noqa: E402
from runtime.ptc.skill_compact_runner import _build_skill_compact_task  # noqa: E402

try:
    import tiktoken
except ImportError:  # pragma: no cover - CI uses the portable fallback
    tiktoken = None

MAX_MODEL_TURNS = 6
SKILL_NAMES = {
    "select_group_evidence_indices",
    "build_timeline",
    "inspect_group_evidence",
}
TOKEN_ESTIMATE_METHOD = "cl100k_base" if tiktoken else "chars_div_4"
MINIMAL_SYSTEM_PROMPT = """You diagnose Android/container failures from compact normalized-log evidence.
Use the single execute_code tool to generate Python that dynamically selects suspect
groups, combines the APIs below, keeps intermediate records inside the sandbox, and
prints only compact evidence needed for your final judgment. You must execute code at
least once. Do not read files, source code, documentation, environment variables, the
snapshot, or the primitive trace. Compact evidence supports primary-failure and
cascading-error candidates; it does not prove a root cause or strict causality.

Work within the provided diagnosis_scope. A primary candidate is the failure most
worth investigating within that scope. Supporting evidence may come from a lower-level
source. If the evidence is insufficient, express the unknown in uncertainties.
recommended_next_checks must contain diagnostic actions only (for example checking
mount, process, service, or runtime state), never automatic remediation. When
cross_source_order="unknown", do not infer strict cross-source temporal causality.

Reusable deterministic evidence Skill:
from tools.android_ptc_diag.skills import inspect_group_evidence
- inspect_group_evidence(group_ids, event_count, boundary_radius=1) ->
  {indices, count, evidence_view}
It resolves compact group IDs using metadata-only group_by_template, selects
bounded boundary indices, calls get_events(indices=...), and returns source-local
evidence_view. Choose suspect group IDs from compact_evidence; do not recreate
group dictionaries, pass raw event indices to this Skill, or read raw evidence
outside it. evidence_view has ordering="source_local",
cross_source_order="unknown", and source buckets; do not infer a global order.

Diagnostic primitives:
from tools.diagnostic import get_events, filter_by_time, filter_by_level, filter_by_text,
    filter_by_tag, group_by_template, first_occurrence, group_by_pid
- get_events(indices=None) -> {events, count}; normally pass explicit indices
- filter_by_time(indices=None, start=None, end=None) -> {indices, count}
- filter_by_level(indices=None, levels=None) -> {indices, count}
- filter_by_text(indices=None, text="") -> {indices, count}
- filter_by_tag(indices=None, tags=None) -> {indices, count}
- group_by_template(indices=None) -> {groups, count}
- first_occurrence(indices=None) -> {indices, count}
- group_by_pid(indices=None) -> {groups, count}

After reading execute_code results, return exactly one JSON object with:
primary_candidate_indices, evidence_indices, propagation_event_indices, uncertainties,
recommended_next_checks, and notes. Do not add a separate proven-cause field or claim
a proven root cause.
All three index fields are integer lists referring to the original normalized events.
"""

SEMANTIC_DECISION_PROMPT = """You are the semantic diagnosis reviewer after an initial Minimal PTC diagnosis.
Return exactly one JSON object matching this contract:
{
  "diagnosis": {
    "primary_candidate_indices": [int],
    "evidence_indices": [int],
    "propagation_event_indices": [int],
    "uncertainties": [string],
    "recommended_next_checks": [string],
    "notes": [string]
  },
  "followup_request": null or {
    "uncertainty": string,
    "check": "inspect_process_state" | "inspect_cgroup_state" | "inspect_service_state" | "inspect_mount_state",
    "arguments": object; inspect_process_state accepts optional string "name" and
      integer "pid" (provide at least one, or both when they identify the same process),
    "reason": string
  }
}

Update candidates and uncertainties from returned evidence. Choose null when the
evidence is sufficient. Follow-ups are read-only diagnostic checks, never remediation.
Do not claim a proven root cause. The controller validates allowlist, arguments,
budget, and duplicate evidence.
"""


class ModelDecisionProvider:
    """Synchronous model-backed provider with per-turn semantic trace data."""

    def __init__(self, model: Any, *, event_count: int):
        self.model = model
        self.event_count = event_count
        self.turns: list[dict[str, Any]] = []

    def __call__(
        self,
        diagnosis: DirectDiagnosis,
        returned_evidence: dict[str, Any] | None,
    ) -> DiagnosisLoopDecision:
        payload = {
            "diagnosis_before": diagnosis.model_dump(),
            "returned_evidence": returned_evidence,
            "instructions": "Update the diagnosis and optionally request one bounded read-only check.",
        }
        messages = [
            {"role": "system", "content": SEMANTIC_DECISION_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        started = time.perf_counter()
        response = self.model.invoke(messages)
        input_tokens, output_tokens = _usage(response)
        response_text = _content(response).strip()
        if response_text.startswith("```"):
            response_text = response_text.strip("`").removeprefix("json").strip()
        decision = DiagnosisLoopDecision.model_validate(json.loads(response_text))
        _validate_diagnosis_indices(decision.diagnosis, self.event_count)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.turns.append(
            {
                "turn": len(self.turns) + 1,
                "diagnosis_before": diagnosis.model_dump(),
                "returned_evidence": returned_evidence,
                "decision": decision.model_dump(),
                "diagnosis_after": decision.diagnosis.model_dump(),
                "followup_request": (
                    decision.followup_request.model_dump()
                    if decision.followup_request
                    else None
                ),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": elapsed_ms,
                "stop_reason": "model chose to stop"
                if decision.followup_request is None
                else None,
            }
        )
        return decision


def _estimate_tokens(value: str) -> int:
    """Portable rough estimate; formal traces use provider-reported usage."""
    if tiktoken:
        return len(tiktoken.get_encoding("cl100k_base").encode(value))
    return math.ceil(len(value) / 4)


def _measure_text(value: str) -> dict[str, int]:
    return {
        "chars": len(value),
        "bytes": len(value.encode("utf-8")),
        "estimated_tokens": _estimate_tokens(value),
    }


def _tool_schema(tool: Any) -> dict[str, Any]:
    schema = tool.get_input_schema().model_json_schema()
    schema.pop("title", None)
    schema.pop("description", None)
    for value in schema.get("properties", {}).values():
        value.pop("title", None)
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": schema,
        },
    }


def fixed_context_measurements(task: str, execute_tool: Any) -> dict[str, Any]:
    schema_text = json.dumps(_tool_schema(execute_tool), ensure_ascii=False, separators=(",", ":"))
    parts = {
        "system_prompt": _measure_text(MINIMAL_SYSTEM_PROMPT),
        "execute_code_schema": _measure_text(schema_text),
        "task": _measure_text(task),
    }
    total_chars = sum(value["chars"] for value in parts.values())
    return {
        **parts,
        "first_turn_total": {
            "chars": total_chars,
            "estimated_tokens": _estimate_tokens(MINIMAL_SYSTEM_PROMPT + schema_text + task),
        },
        "token_estimate_method": TOKEN_ESTIMATE_METHOD,
        "deepagents_first_turn_estimated_tokens": 8884,
    }


def _message_payload(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return message
    return {
        "type": getattr(message, "type", None),
        "content": getattr(message, "content", ""),
        "tool_calls": getattr(message, "tool_calls", None),
        "tool_call_id": getattr(message, "tool_call_id", None),
    }


def _context_metrics(messages: list[Any], tool_schema: dict[str, Any]) -> dict[str, int]:
    message_text = json.dumps(
        [_message_payload(message) for message in messages],
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    schema_text = json.dumps(tool_schema, ensure_ascii=False, separators=(",", ":"))
    request_text = message_text + schema_text
    return {
        "message_count": len(messages),
        "message_chars": len(message_text),
        "message_bytes": len(message_text.encode("utf-8")),
        "tool_schema_chars": len(schema_text),
        "context_chars": len(request_text),
        "context_bytes": len(request_text.encode("utf-8")),
        "context_estimated_tokens": _estimate_tokens(request_text),
    }


def _static_skill_calls(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else (
            node.func.attr if isinstance(node.func, ast.Attribute) else ""
        )
        if name in SKILL_NAMES:
            calls.append(name)
    return calls


def _program_record(code: str) -> dict[str, Any]:
    text = runner.SECRET_PATTERN.sub("<redacted>", code)
    return {
        "sha256": hashlib.sha256(code.encode()).hexdigest(),
        "length": len(code),
        "text": text,
        "static_skill_calls": _static_skill_calls(code),
        "static_primitive_calls": runner._static_program_calls(code),
    }


async def _model_loop(model: Any, execute_tool: Any, task: str) -> dict[str, Any]:
    bound_model = model.bind_tools([execute_tool])
    schema = _tool_schema(execute_tool)
    messages: list[Any] = [
        {"role": "system", "content": MINIMAL_SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    turns: list[dict[str, Any]] = []
    programs: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for turn_number in range(1, MAX_MODEL_TURNS + 1):
        turn = {"turn": turn_number, **_context_metrics(messages, schema)}
        try:
            response = await bound_model.ainvoke(messages)
        except Exception as exc:  # noqa: BLE001 - provider failures belong in the trace
            errors.append(f"model invocation failed: {exc}")
            break
        input_tokens, output_tokens = _usage(response)
        response_text = json.dumps(_message_payload(response), ensure_ascii=False, default=str)
        turn.update(
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "response_chars": len(response_text),
                "response_bytes": len(response_text.encode("utf-8")),
            }
        )
        turns.append(turn)
        messages.append(response)
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            break
        if turn_number == MAX_MODEL_TURNS:
            errors.append("model requested tools after MAX_MODEL_TURNS")
            break
        for call in calls:
            name = str(call.get("name") or "unknown")
            if name != "execute_code":
                errors.append(f"unexpected tool request: {name}")
                continue
            arguments = call.get("args") or {}
            code = str(arguments.get("code") or "")
            programs.append(_program_record(code))
            try:
                content = str(await execute_tool.ainvoke(arguments))
            except Exception as exc:  # noqa: BLE001 - tool failures must return to the model
                content = f"ERROR: {exc}"
            results.append(
                {
                    "bytes": len(content.encode("utf-8")),
                    "success": content.startswith("SUCCESS"),
                    "preview": runner._redact(content),
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "name": "execute_code",
                    "tool_call_id": str(call.get("id") or ""),
                    "content": content,
                }
            )
        if errors:
            break
    return {
        "messages": messages,
        "turns": turns,
        "generated_programs": programs,
        "execute_code_results": results,
        "errors": errors,
    }


async def _download_primitive_trace(sandbox: Any) -> tuple[list[dict[str, Any]], str | None]:
    try:
        content = await sandbox.adownload_file_bytes(
            "/home/daytona/data/primitive-calls.jsonl"
        )
        if content is None:
            raise FileNotFoundError("primitive trace missing")
        return [json.loads(line) for line in content.decode().splitlines() if line.strip()], None
    except Exception as exc:  # noqa: BLE001 - preserve integration diagnostics
        return [], f"runtime primitive trace unavailable: {exc}"


async def run_case(case_dir: Path, trace_file: Path) -> dict[str, Any]:
    from ptc_agent import AgentConfig
    from ptc_agent.agent.tools import create_execute_code_tool
    from ptc_agent.config import MCPServerConfig
    from ptc_agent.core import SessionManager

    request, expected = load_case(case_dir)
    events = ReplayBackend(case_dir).collect_events(request)
    cluster_events(events)
    task = _build_skill_compact_task(request, events)
    snapshot = runner._snapshot_bytes(events, request)
    server = MCPServerConfig(
        name="diagnostic",
        description="Eight deterministic normalized-log primitives.",
        command="uv",
        args=["run", "python", str(runner.DIAGNOSTIC_MCP)],
        env={
            "PTC_SNAPSHOT_FILE": runner.SNAPSHOT_FILE,
            "ANDROID_PTC_SRC": str(REPO_ROOT / "src"),
            "PTC_PRIMITIVE_TRACE_FILE": "/home/daytona/data/primitive-calls.jsonl",
        },
    )
    model = runner._model()
    config = AgentConfig.create(
        llm=model,
        daytona_api_key=os.environ.get("DAYTONA_API_KEY"),
        daytona_base_url=os.environ.get("DAYTONA_BASE_URL", "https://app.daytona.io/api"),
        python_version="3.12",
        snapshot_enabled=False,
        snapshot_auto_create=False,
        mcp_servers=[server],
        max_execution_time=180,
    )
    trace: dict[str, Any] = {
        "case_id": case_dir.name,
        "mode": "minimal_ptc",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "model_parameters": {"temperature": 0, "thinking": "disabled"},
        "upstream_revision": runner.UPSTREAM_REVISION,
        "model_visible_tools": ["execute_code"],
        "model_visible_tool_count": 1,
        "input_event_count": len(events),
        "snapshot_bytes": len(snapshot),
        "generated_programs": [],
        "primitive_calls": [],
        "turns": [],
        "semantic_loop_turns": [],
        "semantic_loop": None,
        "sandbox_startup_ms": None,
        "execution_ms": None,
        "errors": [],
        "invalid": False,
    }
    session_id = f"minimal-ptc-{case_dir.name}-{int(time.time())}"
    session = None
    started = time.perf_counter()
    try:
        session = SessionManager.get_session(session_id, config.to_core_config())
        startup_started = time.perf_counter()
        original_cwd = Path.cwd()
        os.chdir(runner.PTC_RUNTIME_ROOT)
        try:
            await session.initialize()
        finally:
            os.chdir(original_cwd)
        assert session.sandbox is not None and session.mcp_registry is not None
        await runner._upload_shared_modules(
            session.sandbox,
            getattr(session.sandbox, "_work_dir", "/home/daytona"),
            extra_modules=("skills.py",),
        )
        await session.sandbox.sandbox.process.exec("mkdir -p /home/daytona/data")
        if not await session.sandbox.aupload_file_bytes(runner.SNAPSHOT_FILE, snapshot):
            raise RuntimeError("failed to upload diagnostic snapshot")
        trace["sandbox_startup_ms"] = (time.perf_counter() - startup_started) * 1000
        execute_tool = create_execute_code_tool(session.sandbox, session.mcp_registry)
        trace["fixed_context"] = fixed_context_measurements(task, execute_tool)
        execution_started = time.perf_counter()
        loop = await _model_loop(model, execute_tool, task)
        trace["execution_ms"] = (time.perf_counter() - execution_started) * 1000
        trace.update({key: value for key, value in loop.items() if key != "messages"})
        trace["model_turns"] = len(loop["turns"])
        trace["input_tokens"] = sum(t["input_tokens"] or 0 for t in loop["turns"])
        trace["output_tokens"] = sum(t["output_tokens"] or 0 for t in loop["turns"])
        trace["total_tokens"] = trace["input_tokens"] + trace["output_tokens"]
        trace["execute_code_count"] = len(loop["execute_code_results"])
        trace["static_skill_calls"] = [
            name for program in loop["generated_programs"] for name in program["static_skill_calls"]
        ]
        trace["static_primitive_calls"] = [
            call for program in loop["generated_programs"] for call in program["static_primitive_calls"]
        ]
        trace["primitive_calls"], primitive_error = await _download_primitive_trace(
            session.sandbox
        )
        if primitive_error:
            trace["errors"].append(primitive_error)
        diagnosis, diagnosis_error = runner._diagnosis(loop["messages"], len(events))
        if diagnosis_error:
            trace["errors"].append(diagnosis_error)
        if diagnosis:
            provider = ModelDecisionProvider(model, event_count=len(events))
            semantic_state = run_semantic_diagnosis_loop(
                DirectDiagnosis.model_validate(diagnosis),
                load_runtime_state(case_dir),
                provider,
            )
            trace["semantic_loop_turns"] = provider.turns
            trace["semantic_loop"] = semantic_state.model_dump()
            if provider.turns and semantic_state.stop_reason != "model chose to stop":
                provider.turns[-1]["stop_reason"] = semantic_state.stop_reason
            diagnosis = semantic_state.current_diagnosis.model_dump()
            trace["semantic_input_tokens"] = sum(
                turn["input_tokens"] or 0 for turn in provider.turns
            )
            trace["semantic_output_tokens"] = sum(
                turn["output_tokens"] or 0 for turn in provider.turns
            )
        trace["final"] = diagnosis
        if diagnosis:
            candidates = diagnosis["primary_candidate_indices"]
            messages = [events[index].message for index in candidates]
            target = expected["primary_event_contains"]
            trace["top1"] = bool(messages and target in messages[0])
            trace["top3"] = any(target in message for message in messages[:3])
        trace["invalid"] = bool(
            trace["errors"]
            or not trace["generated_programs"]
            or not trace["primitive_calls"]
            or diagnosis is None
        )
    except Exception as exc:  # noqa: BLE001 - integration failures belong in trace
        trace["errors"].append(str(exc))
        trace["invalid"] = True
    finally:
        trace["total_latency_ms"] = (time.perf_counter() - started) * 1000
        if session is not None:
            await SessionManager.cleanup_session(session_id)
    trace_file.parent.mkdir(parents=True, exist_ok=True)
    with trace_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(trace, ensure_ascii=False) + "\n")
    return trace


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run minimal one-tool compact PTC")
    parser.add_argument("--case", required=True)
    parser.add_argument("--trace-file", type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get("DAYTONA_API_KEY"):
        raise SystemExit("DAYTONA_API_KEY is required for a real minimal PTC run")
    case_dir = REPO_ROOT / "eval" / "cases" / args.case
    if not case_dir.is_dir():
        raise SystemExit("unknown replay case")
    trace = await run_case(case_dir, args.trace_file)
    print(json.dumps(trace, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
