from __future__ import annotations

import json
import re
import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from .compact import (
    DEFAULT_DETAILED_GROUP_LIMIT,
    MAX_GROUP_QUERY_BYTES,
    MAX_GROUP_QUERY_RESULTS,
    build_compact_evidence,
    build_two_level_evidence_index,
    compact_model_view,
    query_deferred_groups,
    two_level_model_view,
)
from .evidence_registry import EvidenceReferenceError, EvidenceRegistry
from .models import (
    DirectDiagnosis,
    DirectRunResult,
    DirectTrace,
    FinalValidationIssue,
    LogEvent,
    RegistryBackedDiagnosis,
    RegistryBackedRunResult,
    TriageRequest,
)
from .primitives import (
    tool_definitions,
    tool_handlers,
    typed_reference_tool_definitions,
    typed_reference_tool_handlers,
)


MAX_TOOL_ROUNDS = 6
_SECRET_PATTERN = re.compile(r"(?:sk-|dtn_)[A-Za-z0-9_-]+")
_BEARER_PATTERN = re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]+")
_CREDENTIAL_FIELD_PATTERN = re.compile(
    r'(?i)(["\']?(?:api[_-]?key|access[_-]?token|token|secret|password)'
    r'["\']?\s*[:=]\s*["\'])([^"\']*)(["\'])'
)
_CREDENTIAL_BARE_PATTERN = re.compile(
    r'(?i)(\b(?:api[_-]?key|access[_-]?token|token|secret|password)\b'
    r'\s*[:=]\s*)(?!["\'])([^\s,}]+)'
)

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


def _parse_json_payload(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as original_error:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise original_error
        return json.loads(text[start : end + 1])


def _parse_diagnosis(response: Any) -> DirectDiagnosis:
    return DirectDiagnosis.model_validate(_parse_json_payload(_content(response)))


def _redact_text(value: str) -> str:
    value = _SECRET_PATTERN.sub("<redacted>", value)
    value = _BEARER_PATTERN.sub("Bearer <redacted>", value)
    value = _CREDENTIAL_FIELD_PATTERN.sub(r"\1<redacted>\3", value)
    return _CREDENTIAL_BARE_PATTERN.sub(r"\1<redacted>", value)


def _redact_value(value: Any, key: str | None = None) -> Any:
    if key and re.fullmatch(
        r"(?i)(api[_-]?key|access[_-]?token|token|secret|password)", key
    ):
        return "<redacted>"
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {name: _redact_value(item, name) for name, item in value.items()}
    return value


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


def _validate_diagnosis_indices(diagnosis: DirectDiagnosis, event_count: int) -> None:
    fields = (
        "primary_candidate_indices",
        "evidence_indices",
        "propagation_event_indices",
    )
    for field in fields:
        for position, index in enumerate(getattr(diagnosis, field)):
            if index < 0 or index >= event_count:
                raise EvidenceReferenceError(
                    f"invalid {field} index {index}; expected 0 <= index < {event_count}",
                    [field, position],
                )


class DirectExecutor:
    """Small bounded traditional tool-calling loop over shared primitives."""

    def __init__(self, model: Any, events: list[LogEvent], *, max_tool_rounds: int = MAX_TOOL_ROUNDS):
        self.model = model
        self.events = events
        self.max_tool_rounds = max_tool_rounds
        self.tools = tool_definitions()
        self.handlers = tool_handlers(events)

    final_model: type[BaseModel] = DirectDiagnosis

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
            final_status="not_returned",
        )
        messages: list[Any] = [
            {"role": "system", "content": DIRECT_SYSTEM_PROMPT},
            {"role": "user", "content": self._request_prompt(request)},
        ]
        try:
            model = self.model.bind_tools(self.tools)
            tool_rounds = 0
            while True:
                model_started = time.perf_counter()
                response = model.invoke(messages)
                model_latency_ms = (time.perf_counter() - model_started) * 1000
                trace.model_turns += 1
                input_tokens, output_tokens = _usage(response)
                if input_tokens is not None:
                    trace.input_tokens = (trace.input_tokens or 0) + input_tokens
                if output_tokens is not None:
                    trace.output_tokens = (trace.output_tokens or 0) + output_tokens
                calls = _tool_calls(response)
                trace.model_call_details.append(
                    {
                        "turn": trace.model_turns,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "latency_ms": model_latency_ms,
                        "tool_names": [call["name"] for call in calls],
                        "returned_final": not calls,
                    }
                )
                if not calls:
                    diagnosis = self._audit_final(response, trace)
                    if diagnosis is not None:
                        self._sync_trace_evidence(trace)
                        trace.total_latency_ms = (time.perf_counter() - started) * 1000
                        return self._success_result(diagnosis, trace)
                    break

                if tool_rounds >= self.max_tool_rounds:
                    trace.stopped_by_round_limit = True
                    limit_error = (
                        f"maximum tool rounds reached: {self.max_tool_rounds}; "
                        "model requested another tool round"
                    )
                    trace.error = limit_error
                    trace.errors.append(limit_error)
                    trace.stop_reason = limit_error
                    break

                messages.append(response)
                for call in calls:
                    detail, result = self._execute_call(call)
                    trace.tool_calls += 1
                    trace.tool_call_details.append(detail)
                    if detail.get("error"):
                        trace.error = trace.error or detail["error"]
                        trace.errors.append(detail["error"])
                    model_result = self._prepare_tool_result(call, result)
                    messages.append(
                        _tool_message(
                            call["id"], call["name"] or "unknown", model_result
                        )
                    )
                tool_rounds += 1
                trace.tool_calling_rounds = tool_rounds
        except Exception as exc:  # noqa: BLE001 - preserve a structured trace for provider/tool failures
            message = _redact_text(str(exc))
            trace.error = message
            trace.errors.append(message)
            if trace.final_status == "not_returned":
                trace.stop_reason = "provider or executor exception before final"
        self._sync_trace_evidence(trace)
        trace.total_latency_ms = (time.perf_counter() - started) * 1000
        return self._failure_result(trace)

    def _audit_final(self, response: Any, trace: DirectTrace) -> BaseModel | None:
        raw_text = _content(response)
        trace.final_raw_text = _redact_text(raw_text)
        try:
            payload = _parse_json_payload(raw_text)
        except json.JSONDecodeError as exc:
            self._record_final_error(
                trace,
                status="json_parse_failed",
                stage="json_parse",
                message=f"final model response is not JSON: {exc.msg}",
                path=[],
            )
            return None

        trace.final_parsed_payload = _redact_value(payload)
        try:
            diagnosis = self.final_model.model_validate(payload)
        except ValidationError as exc:
            trace.final_status = "schema_validation_failed"
            for error in exc.errors(include_input=False, include_url=False):
                message = error["msg"]
                path = list(error.get("loc", ()))
                trace.final_validation_errors.append(
                    FinalValidationIssue(
                        stage="schema_validation", message=message, path=path
                    )
                )
                trace.errors.append(message)
            trace.error = trace.error or _redact_text(str(exc))
            return None

        try:
            self._validate_final(diagnosis)
        except EvidenceReferenceError as exc:
            self._record_final_error(
                trace,
                status="reference_validation_failed",
                stage="reference_validation",
                message=str(exc),
                path=exc.path,
            )
            return None

        trace.final_status = "accepted"
        return diagnosis

    @staticmethod
    def _record_final_error(
        trace: DirectTrace,
        *,
        status: str,
        stage: str,
        message: str,
        path: list[str | int],
    ) -> None:
        trace.final_status = status
        trace.final_validation_errors.append(
            FinalValidationIssue(stage=stage, message=message, path=path)
        )
        trace.error = trace.error or message
        trace.errors.append(message)

    def _validate_final(self, diagnosis: BaseModel) -> None:
        _validate_diagnosis_indices(diagnosis, len(self.events))

    def _prepare_tool_result(
        self, call: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        return result

    def _trace_evidence(self) -> list[Any]:
        return []

    def _sync_trace_evidence(self, trace: DirectTrace) -> None:
        trace.evidence_registry = self._trace_evidence()

    def _success_result(self, diagnosis: BaseModel, trace: DirectTrace) -> DirectRunResult:
        trace.primary_candidate_indices = diagnosis.primary_candidate_indices
        trace.evidence_indices = diagnosis.evidence_indices
        trace.propagation_event_indices = diagnosis.propagation_event_indices
        return DirectRunResult(diagnosis=diagnosis, trace=trace)

    def _failure_result(self, trace: DirectTrace) -> DirectRunResult:
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
            "tool_call_id": call.get("id"),
            "tool": name,
            "arguments": args,
            "record_count": _record_count(result),
            "result_bytes": len(serialized.encode("utf-8")),
            "latency_ms": (time.perf_counter() - started) * 1000,
        }
        if error:
            detail["error"] = error
        return detail, result


class CompactDirectExecutor(DirectExecutor):
    """DIRECT execution starting from deterministic compact evidence."""

    def __init__(
        self,
        model: Any,
        events: list[LogEvent],
        *,
        baseline_indices: list[int] | None = None,
        incident_indices: list[int] | None = None,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
    ):
        super().__init__(model, events, max_tool_rounds=max_tool_rounds)
        self.baseline_indices = baseline_indices
        self.incident_indices = incident_indices

    def _request_prompt(self, request: TriageRequest) -> str:
        comparison_requested = (
            self.baseline_indices is not None or self.incident_indices is not None
        )
        compact = compact_model_view(
            build_compact_evidence(
                self.events,
                baseline_indices=self.baseline_indices,
                incident_indices=self.incident_indices,
            )
        )
        payload = {
            "symptom": request.symptom,
            "target_package": request.target_package,
            "failure_timestamp": request.failure_timestamp.isoformat()
            if request.failure_timestamp
            else None,
            "raw_event_count": len(self.events),
            "compact_group_count": compact["group_count"],
            "compact_evidence": compact,
            "output_schema": {
                "primary_candidate_indices": "list[int]",
                "evidence_indices": "list[int]",
                "propagation_event_indices": "list[int]",
                "notes": "list[str]",
            },
        }
        if comparison_requested:
            baseline_count = len(set(self.baseline_indices or []))
            incident_count = len(set(self.incident_indices or []))
            payload.update(
                {
                    "target_component": request.target_component,
                    "expected_behavior": request.expected_behavior,
                    "observed_behavior": request.observed_behavior,
                    "incident_context": {
                        "incident_time": request.incident_time.isoformat()
                        if request.incident_time
                        else None,
                        "baseline_event_count": baseline_count,
                        "incident_event_count": incident_count,
                        "unassigned_event_count": len(self.events)
                        - baseline_count
                        - incident_count,
                    },
                }
            )
        instructions = (
            "The compact evidence is a deterministic, provenance-preserving summary "
            "covering all raw events. Raw events remain available through the diagnostic "
            "primitives. Start diagnosis from the compact evidence. Use get_events with "
            "specific indices when original event text is needed. Count is the group size; "
            "first_index and last_index bound its occurrences; representative_index is its "
            "first raw event. Do not assume the compact representation itself proves causality."
        )
        return instructions + "\n" + json.dumps(payload, ensure_ascii=False)


class RegistryBackedCompactDirectExecutor(CompactDirectExecutor):
    """Opt-in compact DIRECT path with a system-owned evidence registry."""

    final_model = RegistryBackedDiagnosis

    def __init__(
        self,
        *args: Any,
        runtime_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.evidence_registry = EvidenceRegistry()
        if runtime_state is not None:
            self.tools = typed_reference_tool_definitions()
            self.handlers = typed_reference_tool_handlers(self.events, runtime_state)

    def run(self, *args: Any, **kwargs: Any) -> RegistryBackedRunResult:
        self.evidence_registry = EvidenceRegistry()
        return super().run(*args, **kwargs)

    def _request_prompt(self, request: TriageRequest) -> str:
        base_prompt = super()._request_prompt(request)
        instructions, serialized = base_prompt.rsplit("\n", 1)
        payload = json.loads(serialized)
        items = self.evidence_registry.add_compact_view(payload["compact_evidence"])
        payload["evidence_registry"] = [
            item.model_dump(mode="json") for item in items
        ]
        payload["output_schema"] = RegistryBackedDiagnosis.model_json_schema()
        registry_instructions = (
            " EvidenceItem records are system-generated from only the evidence shown "
            "in this run. Cite their evidence_id values in claim evidence_refs. Do not "
            "return or rewrite an evidence_items registry."
        )
        return instructions + registry_instructions + "\n" + json.dumps(
            payload, ensure_ascii=False
        )

    def _prepare_tool_result(
        self, call: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        items = self.evidence_registry.add_tool_result(
            tool_call_id=str(call["id"]),
            tool_name=call["name"] or "unknown",
            result=result,
        )
        if not items:
            return result
        return {
            **result,
            "_system_evidence_items": [
                item.model_dump(mode="json") for item in items
            ],
        }

    def _validate_final(self, diagnosis: BaseModel) -> None:
        self.evidence_registry.validate_references(diagnosis)

    def _trace_evidence(self) -> list[Any]:
        return self.evidence_registry.snapshot()

    def _success_result(
        self, diagnosis: BaseModel, trace: DirectTrace
    ) -> RegistryBackedRunResult:
        return RegistryBackedRunResult(
            diagnosis=diagnosis,
            evidence_registry=self.evidence_registry.snapshot(),
            trace=trace,
        )

    def _failure_result(self, trace: DirectTrace) -> RegistryBackedRunResult:
        return RegistryBackedRunResult(
            evidence_registry=self.evidence_registry.snapshot(), trace=trace
        )


class TwoLevelCompactDirectExecutor(CompactDirectExecutor):
    """Opt-in Compact-DIRECT with bounded initial detail and deferred lookup."""

    def __init__(
        self,
        *args: Any,
        detailed_group_limit: int = DEFAULT_DETAILED_GROUP_LIMIT,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.detailed_group_limit = detailed_group_limit
        self.evidence_index: dict[str, Any] | None = None
        self.tools.append(
            {
                "type": "function",
                "function": {
                    "name": "query_deferred_groups",
                    "description": (
                        "Read bounded compact summaries from groups omitted from the "
                        "initial detailed evidence. Deferred groups are not noise."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "group_ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                            "text": {"type": "string"},
                            "tag": {"type": "string"},
                            "source": {"type": "string"},
                            "phase": {
                                "type": "string",
                                "enum": ["baseline", "incident", "unassigned"],
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": MAX_GROUP_QUERY_RESULTS,
                            },
                            "max_bytes": {
                                "type": "integer",
                                "minimum": 256,
                                "maximum": MAX_GROUP_QUERY_BYTES,
                            },
                        },
                    },
                },
            }
        )

    def _request_prompt(self, request: TriageRequest) -> str:
        self.evidence_index = build_two_level_evidence_index(
            self.events,
            request,
            baseline_indices=self.baseline_indices,
            incident_indices=self.incident_indices,
            detailed_group_limit=self.detailed_group_limit,
        )
        self.handlers["query_deferred_groups"] = lambda **kwargs: query_deferred_groups(
            self.evidence_index, **kwargs
        )
        view = two_level_model_view(self.evidence_index)
        payload = {
            "symptom": request.symptom,
            "target_package": request.target_package,
            "failure_timestamp": request.failure_timestamp.isoformat()
            if request.failure_timestamp
            else None,
            "raw_event_count": len(self.events),
            "compact_group_count": view["group_count"],
            "two_level_evidence": view,
            "output_schema": {
                "primary_candidate_indices": "list[int]",
                "evidence_indices": "list[int]",
                "propagation_event_indices": "list[int]",
                "notes": "list[str]",
            },
        }
        if self.baseline_indices is not None or self.incident_indices is not None:
            baseline_count = len(set(self.baseline_indices or []))
            incident_count = len(set(self.incident_indices or []))
            payload.update(
                {
                    "target_component": request.target_component,
                    "expected_behavior": request.expected_behavior,
                    "observed_behavior": request.observed_behavior,
                    "incident_context": {
                        "incident_time": request.incident_time.isoformat()
                        if request.incident_time
                        else None,
                        "baseline_event_count": baseline_count,
                        "incident_event_count": incident_count,
                        "unassigned_event_count": len(self.events)
                        - baseline_count
                        - incident_count,
                    },
                }
            )
        instructions = (
            "Detailed evidence is a bounded deterministic index, not a complete "
            "diagnosis and not a noise classifier. Deferred groups remain queryable "
            "with query_deferred_groups using stable global group IDs. Expand the "
            "search when the detailed groups do not explain the symptom, then use "
            "get_events with specific indices for original normalized records."
        )
        return instructions + "\n" + json.dumps(payload, ensure_ascii=False)


class PtcExecutor:
    """Thin adapter around an existing PTC runtime (target: open-ptc-agent)."""

    def run(self, *args, **kwargs):
        raise NotImplementedError("M3: integrate mature PTC backend")


class HybridExecutor:
    """M4: PTC triage + at most one direct evidence-acquisition round."""

    max_investigation_rounds = 1

    def run(self, *args, **kwargs):
        raise NotImplementedError("M4: implement after PTC badcases exist")
