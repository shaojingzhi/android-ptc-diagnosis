"""Run one registry-backed Compact-DIRECT model evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from android_ptc_diag.backend import ReplayBackend
from android_ptc_diag.cases import load_case, load_runtime_state
from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.execution import (
    DIRECT_SYSTEM_PROMPT,
    RegistryBackedCompactDirectExecutor,
)


DEFAULT_CASE_ID = "runtime_service_unready_001"
MODEL_NAME = "deepseek-v4-pro"


def _model() -> Any:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required")
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise SystemExit(
            'Install the optional DIRECT dependency with: pip install -e ".[direct]"'
        ) from exc
    return ChatOpenAI(
        model=MODEL_NAME,
        api_key=api_key,
        base_url="https://api.deepseek.com",
        temperature=0,
        max_retries=0,
        extra_body={"thinking": {"type": "disabled"}},
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def prepare_evaluation(
    root: Path,
    case_id: str,
    *,
    reliability_hardening: bool = False,
    deduplicated_evidence_view: bool = False,
) -> dict[str, Any]:
    case_dir = root / "eval" / "cases" / case_id
    if not case_dir.is_dir() or case_dir.parent != root / "eval" / "cases":
        raise ValueError(f"unknown replay case: {case_id}")
    request, _ = load_case(case_dir)
    runtime_state = load_runtime_state(case_dir)
    events = ReplayBackend(case_dir).collect_events(request)
    cluster_events(events)
    executor = RegistryBackedCompactDirectExecutor(
        None,
        events,
        runtime_state=runtime_state,
        reliability_hardening=reliability_hardening,
        deduplicated_evidence_view=deduplicated_evidence_view,
    )
    task_prompt = executor._request_prompt(request)
    return {
        "evaluation_case": case_id,
        "request": request,
        "runtime_state": runtime_state,
        "events": events,
        "task_prompt": task_prompt,
        "tool_schema": executor.tools,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default=DEFAULT_CASE_ID)
    parser.add_argument("--incident-id")
    parser.add_argument("--implementation-commit")
    parser.add_argument("--reliability-hardening", action="store_true")
    parser.add_argument("--deduplicated-evidence-view", action="store_true")
    parser.add_argument("--trace-file", type=Path, required=True)
    args = parser.parse_args()
    if args.deduplicated_evidence_view and not args.reliability_hardening:
        parser.error("--deduplicated-evidence-view requires --reliability-hardening")
    if args.trace_file.exists():
        raise SystemExit(f"refusing to overwrite trace: {args.trace_file}")

    root = Path(__file__).parents[1]
    incident_id = args.incident_id or args.case
    prepared = prepare_evaluation(
        root,
        args.case,
        reliability_hardening=args.reliability_hardening,
        deduplicated_evidence_view=args.deduplicated_evidence_view,
    )
    repository_commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    implementation_commit = args.implementation_commit or repository_commit

    result = RegistryBackedCompactDirectExecutor(
        _model(),
        prepared["events"],
        runtime_state=prepared["runtime_state"],
        reliability_hardening=args.reliability_hardening,
        deduplicated_evidence_view=args.deduplicated_evidence_view,
    ).run(
        prepared["request"],
        case_id=incident_id,
        model_metadata={
            "model": MODEL_NAME,
            "provider": "deepseek",
            "parameters": {
                "temperature": 0,
                "thinking": "disabled",
                "max_tool_rounds": 6,
                "provider_max_retries": 0,
            },
        },
    )
    trace = result.trace.model_dump(mode="json")
    trace.update(
        {
            "experiment": "registry_backed_real_model_smoke",
            "evaluation_case": prepared["evaluation_case"],
            "neutral_incident_id": incident_id,
            "implementation_commit": implementation_commit,
            "repository_commit": repository_commit,
            "system_prompt": DIRECT_SYSTEM_PROMPT,
            "system_prompt_sha256": _sha256(DIRECT_SYSTEM_PROMPT),
            "task_prompt": prepared["task_prompt"],
            "task_prompt_sha256": _sha256(prepared["task_prompt"]),
            "task_prompt_bytes": len(prepared["task_prompt"].encode("utf-8")),
            "tool_names": [
                item["function"]["name"] for item in prepared["tool_schema"]
            ],
            "tool_schema_sha256": _sha256(
                _canonical_json(prepared["tool_schema"])
            ),
            "provider_retry_policy": {"max_retries": 0},
            "reliability_hardening": args.reliability_hardening,
            "deduplicated_evidence_view": args.deduplicated_evidence_view,
            "total_tokens": (
                trace["input_tokens"] + trace["output_tokens"]
                if trace["input_tokens"] is not None
                and trace["output_tokens"] is not None
                else None
            ),
        }
    )
    args.trace_file.parent.mkdir(parents=True, exist_ok=True)
    with args.trace_file.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(trace, ensure_ascii=False) + "\n")

    summary = {
        key: trace[key]
        for key in (
            "case_id",
            "implementation_commit",
            "final_status",
            "model_turns",
            "tool_calling_rounds",
            "tool_calls",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "total_latency_ms",
            "stop_reason",
            "errors",
        )
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
