"""Run the provenance-preserving Compact-DIRECT stress experiment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from android_ptc_diag.backend import ReplayBackend
from android_ptc_diag.cases import load_case
from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.compact import (
    build_compact_evidence,
    compact_model_view,
    evidence_recall,
)
from android_ptc_diag.execution import CompactDirectExecutor


CASE_ID = "binder_flood_001"
GROUND_TRUTH_EVIDENCE = [20, 21, 371, 499]


def _model() -> Any:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required")
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise SystemExit('Install the optional DIRECT dependency with: pip install -e ".[direct]"') from exc
    return ChatOpenAI(
        model="deepseek-v4-pro",
        api_key=api_key,
        base_url="https://api.deepseek.com",
        temperature=0,
        extra_body={"thinking": {"type": "disabled"}},
    )


def _json_bytes(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _tool_metrics(details: list[dict[str, Any]]) -> dict[str, int]:
    get_events_calls = [detail for detail in details if detail["tool"] == "get_events"]
    full_calls = [
        detail
        for detail in get_events_calls
        if detail.get("arguments", {}).get("indices") is None
    ]
    indexed_calls = [detail for detail in get_events_calls if detail not in full_calls]
    return {
        "get_events_total_calls": len(get_events_calls),
        "get_events_full_calls": len(full_calls),
        "get_events_indexed_calls": len(indexed_calls),
        "largest_get_events_record_count": max(
            (detail.get("record_count") or 0 for detail in get_events_calls), default=0
        ),
        "largest_tool_result_bytes": max(
            (detail.get("result_bytes") or 0 for detail in details), default=0
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Compact-DIRECT stress experiment")
    parser.add_argument("--trace-file", type=Path, required=True)
    args = parser.parse_args()

    case_dir = Path(__file__).parents[1] / "eval" / "cases" / CASE_ID
    request, expected = load_case(case_dir)
    events = ReplayBackend(case_dir).collect_events(request)
    cluster_events(events)
    compact = build_compact_evidence(events)
    model_view = compact_model_view(compact)
    recall = evidence_recall(compact, GROUND_TRUTH_EVIDENCE)
    if recall != 1.0:
        raise SystemExit(f"compact evidence recall gate failed: {recall}")

    raw_representation = {
        "events": [event.model_dump(mode="json") for event in events],
        "request": request.model_dump(mode="json"),
    }
    raw_bytes = _json_bytes(raw_representation)
    compact_bytes = _json_bytes(model_view)
    result = CompactDirectExecutor(_model(), events).run(
        request,
        case_id=CASE_ID,
        model_metadata={
            "model": "deepseek-v4-pro",
            "provider": "deepseek",
            "parameters": {"temperature": 0, "thinking": "disabled"},
        },
    )

    diagnosis = result.diagnosis
    expected_text = expected["primary_event_contains"]
    primary_messages = (
        [events[index].message for index in diagnosis.primary_candidate_indices]
        if diagnosis
        else []
    )
    trace = result.trace.model_dump()
    trace.update(
        {
            "mode": "compact_direct",
            "raw_event_count": len(events),
            "compact_group_count": compact["group_count"],
            "raw_representation_bytes": raw_bytes,
            "compact_view_bytes": compact_bytes,
            "compression_ratio": raw_bytes / compact_bytes,
            "evidence_recall": recall,
            "total_tokens": (trace["input_tokens"] or 0) + (trace["output_tokens"] or 0),
            "final": diagnosis.model_dump() if diagnosis else None,
            "top1": bool(primary_messages and expected_text in primary_messages[0])
            if diagnosis
            else None,
            "top3": any(expected_text in message for message in primary_messages[:3])
            if diagnosis
            else None,
        }
    )
    trace.update(_tool_metrics(trace["tool_call_details"]))
    args.trace_file.parent.mkdir(parents=True, exist_ok=True)
    with args.trace_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(trace, ensure_ascii=False) + "\n")
    print(json.dumps(trace, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
