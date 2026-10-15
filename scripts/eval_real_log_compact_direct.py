"""Run one audited Compact-DIRECT smoke over a local real Logcat capture."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

import yaml

from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.compact import build_compact_evidence, compact_model_view
from android_ptc_diag.execution import CompactDirectExecutor
from android_ptc_diag.models import LogEvent, TriageRequest
from android_ptc_diag.parsing import parse_logcat
from android_ptc_diag.triage import event_indices_in_interval
from scripts.eval_compact_direct import _json_bytes, _model, _tool_metrics


CASE_ID = "locally_reproduced_java_crash_001"
EXPECTED_COUNTS = {
    "raw_lines": 549,
    "normalized_events": 545,
    "compact_groups": 275,
    "baseline_events": 14,
    "incident_events": 371,
    "unassigned_events": 160,
}
_REDACTIONS = (
    (re.compile(r"(?:sk-|dtn_)[A-Za-z0-9_-]{16,}"), "<credential>"),
    (re.compile(r"/Users/[^\s:,)}\]]+"), "<host-path>"),
    (re.compile(r"/(?:data|storage|sdcard)/[^\s:,)}\]]+"), "<private-path>"),
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "<email>"),
    (re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"), "<ip-address>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b"), "<uuid>"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<hex-id>"),
    (re.compile(r"(?<!\d)\d{8,}(?!\d)"), "<numeric-id>"),
)


def _sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    for pattern, replacement in _REDACTIONS:
        value = pattern.sub(replacement, value)
    return value


def _sanitize_events(
    events: list[LogEvent],
) -> tuple[list[LogEvent], list[dict[str, Any]]]:
    process_ids = sorted(
        {value for event in events for value in (event.pid, event.tid) if value is not None}
    )
    pseudonyms = {value: 1000 + index for index, value in enumerate(process_ids)}
    sanitized: list[LogEvent] = []
    index_map: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        original = event.message
        copy = event.model_copy(deep=True)
        copy.pid = pseudonyms.get(event.pid)
        copy.tid = pseudonyms.get(event.tid)
        copy.tag = _sanitize_text(event.tag)
        copy.component = _sanitize_text(event.component)
        copy.message = _sanitize_text(event.message) or ""
        copy.template = _sanitize_text(event.template)
        sanitized.append(copy)
        index_map.append(
            {
                "normalized_index": index,
                "raw_line_number": event.raw_line_number,
                "original_message_sha256": hashlib.sha256(original.encode()).hexdigest(),
                "sanitized_message_sha256": hashlib.sha256(copy.message.encode()).hexdigest(),
                "message_changed": original != copy.message,
                "changed": original != copy.message
                or event.pid != copy.pid
                or event.tid != copy.tid,
            }
        )
    return sanitized, index_map


def _write_reviewed_copy(
    events: list[LogEvent],
    index_map: list[dict[str, Any]],
    events_path: Path,
    map_path: Path,
) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    serialized_events = "\n".join(event.model_dump_json() for event in events) + "\n"
    _assert_reviewed_text(serialized_events)
    events_path.write_text(serialized_events, encoding="utf-8")
    map_path.write_text(
        "\n".join(json.dumps(item, sort_keys=True) for item in index_map) + "\n",
        encoding="utf-8",
    )


def _assert_reviewed_text(value: str) -> None:
    for pattern, _ in _REDACTIONS:
        if pattern.search(value):
            raise ValueError(f"sanitized copy failed audit pattern: {pattern.pattern}")


def _prepare_capture(
    raw_path: Path,
    metadata_path: Path,
    sanitized_path: Path,
    map_path: Path,
) -> tuple[TriageRequest, list[LogEvent], list[int], list[int], dict[str, Any]]:
    raw_text = raw_path.read_text(encoding="utf-8")
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    expected_hash = metadata["raw_log"]["sha256"]
    actual_hash = hashlib.sha256(raw_text.encode()).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError("raw capture sha256 does not match metadata")

    events = parse_logcat(
        raw_text,
        year=int(metadata["captured_on"].split("-", 1)[0]),
        device_timezone=metadata["device"]["timezone"],
    )
    boundaries = metadata["boundaries"]
    baseline_indices = event_indices_in_interval(
        events,
        start=datetime.fromisoformat(boundaries["baseline_start"]),
        end=datetime.fromisoformat(boundaries["baseline_end"]),
    )
    incident_indices = event_indices_in_interval(
        events,
        start=datetime.fromisoformat(boundaries["incident_start"]),
        end=datetime.fromisoformat(boundaries["incident_end"]),
    )
    cluster_events(events)
    compact = build_compact_evidence(
        events,
        baseline_indices=baseline_indices,
        incident_indices=incident_indices,
    )
    stats = {
        "raw_lines": len(raw_text.splitlines()),
        "normalized_events": len(events),
        "compact_groups": compact["group_count"],
        "baseline_events": len(baseline_indices),
        "incident_events": len(incident_indices),
        "unassigned_events": len(events) - len(baseline_indices) - len(incident_indices),
    }
    if stats != EXPECTED_COUNTS:
        raise ValueError(f"offline validation mismatch: {stats}")

    crash_indices = [
        index
        for index, event in enumerate(events)
        if "FATAL EXCEPTION" in event.message or "M1 deliberate Java crash" in event.message
    ]
    if crash_indices != [119, 121, 136]:
        raise ValueError(f"unexpected crash evidence indices: {crash_indices}")

    sanitized, index_map = _sanitize_events(events)
    sanitized_compact = build_compact_evidence(
        sanitized,
        baseline_indices=baseline_indices,
        incident_indices=incident_indices,
    )
    if sanitized_compact["group_count"] != EXPECTED_COUNTS["compact_groups"]:
        raise ValueError("sanitization changed compact group count")

    incident_start = datetime.fromisoformat(boundaries["incident_start"])
    request = TriageRequest(
        symptom="The locally reproduced test app crashes immediately during launch",
        target_package=metadata["fixture"]["package"],
        failure_timestamp=incident_start,
        incident_time=incident_start,
        target_component="MainActivity",
        expected_behavior="The test Activity launches and remains running",
        observed_behavior="The test Activity terminates during startup with a Java exception",
    )
    model_task = CompactDirectExecutor(
        object(),
        sanitized,
        baseline_indices=baseline_indices,
        incident_indices=incident_indices,
    )._request_prompt(request)
    _assert_reviewed_text(model_task)
    _write_reviewed_copy(sanitized, index_map, sanitized_path, map_path)
    stats.update(
        {
            "crash_evidence_indices": crash_indices,
            "message_redacted_event_count": sum(
                item["message_changed"] for item in index_map
            ),
            "pseudonymized_event_count": sum(item["changed"] for item in index_map),
            "sanitized_view_bytes": _json_bytes(compact_model_view(sanitized_compact)),
            "model_task_bytes": len(model_task.encode("utf-8")),
        }
    )
    return request, sanitized, baseline_indices, incident_indices, stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-logcat", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--sanitized-events", type=Path, required=True)
    parser.add_argument("--index-map", type=Path, required=True)
    parser.add_argument("--trace-file", type=Path)
    parser.add_argument("--run-model", action="store_true")
    args = parser.parse_args()

    request, events, baseline, incident, stats = _prepare_capture(
        args.raw_logcat,
        args.metadata,
        args.sanitized_events,
        args.index_map,
    )
    if not args.run_model:
        print(json.dumps({"offline_validation": stats}, indent=2))
        return
    if args.trace_file is None:
        raise SystemExit("--trace-file is required with --run-model")

    result = CompactDirectExecutor(
        _model(),
        events,
        baseline_indices=baseline,
        incident_indices=incident,
    ).run(
        request,
        case_id=CASE_ID,
        model_metadata={
            "model": "deepseek-v4-pro",
            "provider": "deepseek",
            "parameters": {"temperature": 0, "thinking": "disabled"},
        },
    )
    trace = result.trace.model_dump()
    diagnosis = result.diagnosis
    primary_indices = diagnosis.primary_candidate_indices if diagnosis else []
    primary_summaries = [
        {
            "index": index,
            "raw_line_number": events[index].raw_line_number,
            "level": events[index].level,
            "tag": events[index].tag,
            "message": events[index].message,
        }
        for index in primary_indices
    ]
    crash_indices = set(stats["crash_evidence_indices"])
    trace.update(stats)
    trace.update(_tool_metrics(trace["tool_call_details"]))
    trace.update(
        {
            "mode": "real_log_compact_direct",
            "git_commit": subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], text=True
            ).strip(),
            "total_tokens": (trace["input_tokens"] or 0)
            + (trace["output_tokens"] or 0),
            "final": trace["final_parsed_payload"] if diagnosis else None,
            "primary_event_summaries": primary_summaries,
            "java_crash_located": any(index in crash_indices for index in primary_indices),
            "indices_valid": diagnosis is not None,
        }
    )
    args.trace_file.parent.mkdir(parents=True, exist_ok=True)
    args.trace_file.write_text(json.dumps(trace, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(trace, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
