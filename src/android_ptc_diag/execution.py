from __future__ import annotations

import json
import time
from enum import StrEnum
from typing import Any

from .models import DirectDiagnosis, DirectRunResult, DirectTrace, LogEvent, TriageRequest
from .primitives import tool_definitions, tool_handlers


MAX_TOOL_ROUNDS = 6

DIRECT_SYSTEM_PROMPT = """You are diagnosing a noisy Android/container log replay.
Use only the provided deterministic evidence primitives. Inspect evidence before
choosing primary failure candidates, and describe downstream events as candidates
for propagation rather than proven causality. Do not claim strict causal inference.
Return only JSON matching the requested diagnosis schema when you finish."""


class ExecutionMode(StrEnum):
    # RAW and DIRECT are intentionally separate baselines.
    RAW = "raw"
    FIXED = "fixed"
    DIRECT = "direct"
    PTC = "ptc"
    HYBRID = "hybrid"


def _content(response: Any) -> str:
    if isinstance(response, dict):
        value = response.get("content", "")
    else:
        value = getattr(response, "content", "")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            part.get("text", "") for part in value if isinstance(part, dict) and part.get("text")
        )
    return str(value)


def _tool_calls(response: Any) -> list[dict[str, Any]]:
    calls = getattr(response, "tool_calls", None)
    if calls is None and isinstance(response, dict):
        calls = response.get("tool_calls")
    if calls is None and hasattr(response, "additional_kwargs"):
        additional_kwargs = response.additional_kwargs or {}
        calls = additional_kwargs.get("tool_calls")
    normalized = []
    for raw in calls or []:
        function = raw.get("function", {}) if isinstance(raw, dict) else {}
        name = raw.get("name") or function.get("name")
        args = raw.get("args", function.get("arguments", {}))
        if isinstance(args, str):
            args = json.loads(args)
        normalized.append(
            {
                "id": raw.get("id", f"tool-{len(normalized)}"),
                "name": name,
                "args": args or {},
            }
        )
    return normalized


def _tool_message(call_id: str, name: str, result: dict[str, Any]) -> Any:
    content = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    try:
        from langchain_core.messages import ToolMessage

        return ToolMessage(content=content, tool_call_id=call_id, name=name)
    except ImportError:
        return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


def _parse_diagnosis(response: Any) -> DirectDiagnosis:
    text = _content(response).strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("final model response is not JSON") from None
        payload = json.loads(text[start : end + 1])
    return DirectDiagnosis.model_validate(payload)


def _usage(response: Any) -> tuple[int | None, int | None]:
    metadata = getattr(response, "usage_metadata", None)
    if not metadata and hasattr(response, "response_metadata"):
        metadata = (response.response_metadata or {}).get("token_usage")
    if not isinstance(metadata, dict):
        return None, None
    return metadata.get("input_tokens", metadata.get("prompt_tokens")), metadata.get(
        "output_tokens", metadata.get("completion_tokens")
    )


def _record_count(value: Any) -> int | None:
    if isinstance(value, dict):
        if isinstance(value.get("count"), int):
            return value["count"]
        if isinstance(value.get("events"), list):
            return len(value["events"])
        if isinstance(value.get("indices"), list):
            return len(value["indices"])
    return None


class DirectExecutor:
    """Small bounded traditional tool-calling loop over shared primitives."""

    def __init__(self, model: Any, events: list[LogEvent], *, max_tool_rounds: int = MAX_TOOL_ROUNDS):
        self.model = model
        self.events = events
        self.max_tool_rounds = max_tool_rounds
        self.tools = tool_definitions()
        self.handlers = tool_handlers(events)

    def run(
        self,
        request: TriageRequest,
        *,
        case_id: str | None = None,
        model_metadata: dict[str, Any] | None = None,
    ) -> DirectRunResult:
        started = time.perf_counter()
        trace = DirectTrace(
            case_id=case_id,
            input_event_count=len(self.events),
            model=(model_metadata or {}).get("model"),
            provider=(model_metadata or {}).get("provider"),
            model_parameters=(model_metadata or {}).get("parameters", {}),
        )
        messages: list[Any] = [
            {"role": "system", "content": DIRECT_SYSTEM_PROMPT},
            {"role": "user", "content": self._request_prompt(request)},
        ]
        try:
            model = self.model.bind_tools(self.tools)
            for round_index in range(self.max_tool_rounds):
                response = model.invoke(messages)
                trace.model_turns += 1
                input_tokens, output_tokens = _usage(response)
                if input_tokens is not None:
                    trace.input_tokens = (trace.input_tokens or 0) + input_tokens
                if output_tokens is not None:
                    trace.output_tokens = (trace.output_tokens or 0) + output_tokens
                calls = _tool_calls(response)
                if not calls:
                    diagnosis = _parse_diagnosis(response)
                    trace.primary_candidate_indices = diagnosis.primary_candidate_indices
                    trace.evidence_indices = diagnosis.evidence_indices
                    trace.propagation_event_indices = diagnosis.propagation_event_indices
                    trace.total_latency_ms = (time.perf_counter() - started) * 1000
                    return DirectRunResult(diagnosis=diagnosis, trace=trace)

                messages.append(response)
                for call in calls:
                    detail, result = self._execute_call(call)
                    trace.tool_calls += 1
                    trace.tool_call_details.append(detail)
                    if detail.get("error"):
                        trace.error = trace.error or detail["error"]
                        trace.errors.append(detail["error"])
                    messages.append(_tool_message(call["id"], call["name"] or "unknown", result))
                if round_index == self.max_tool_rounds - 1:
                    trace.stopped_by_round_limit = True
                    limit_error = f"maximum tool rounds reached: {self.max_tool_rounds}"
                    trace.error = limit_error
                    trace.errors.append(limit_error)
                    break
        except Exception as exc:  # noqa: BLE001 - preserve a structured trace for provider/tool failures
            trace.error = str(exc)
            trace.errors.append(str(exc))
        trace.total_latency_ms = (time.perf_counter() - started) * 1000
        return DirectRunResult(trace=trace)

    def _request_prompt(self, request: TriageRequest) -> str:
        payload = {
            "symptom": request.symptom,
            "target_package": request.target_package,
            "failure_timestamp": request.failure_timestamp.isoformat() if request.failure_timestamp else None,
            "event_count": len(self.events),
            "output_schema": {
                "primary_candidate_indices": "list[int]",
                "evidence_indices": "list[int]",
                "propagation_event_indices": "list[int]",
                "notes": "list[str]",
            },
        }
        return "Inspect the replay evidence using primitives and return a diagnosis JSON.\n" + json.dumps(payload)

    def _execute_call(self, call: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        name = call.get("name")
        args = call.get("args") or {}
        started = time.perf_counter()
        try:
            if name not in self.handlers:
                raise ValueError(f"unknown diagnostic primitive: {name}")
            result = self.handlers[name](**args)
            error = None
        except Exception as exc:  # noqa: BLE001 - return errors to the model as tool results
            result = {"error": str(exc)}
            error = str(exc)
        serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        detail = {
            "tool": name,
            "arguments": args,
            "record_count": _record_count(result),
            "result_bytes": len(serialized.encode("utf-8")),
            "latency_ms": (time.perf_counter() - started) * 1000,
        }
        if error:
            detail["error"] = error
        return detail, result


class PtcExecutor:
    """Thin adapter around an existing PTC runtime (target: open-ptc-agent)."""

    def run(self, *args, **kwargs):
        raise NotImplementedError("M3: integrate mature PTC backend")


class HybridExecutor:
    """M4: PTC triage + at most one direct evidence-acquisition round."""

    max_investigation_rounds = 1

    def run(self, *args, **kwargs):
        raise NotImplementedError("M4: implement after PTC badcases exist")
