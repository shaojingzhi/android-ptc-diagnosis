"""Run the single registry-backed Compact-DIRECT model smoke."""

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


CASE_ID = "runtime_service_unready_001"
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
        extra_body={"thinking": {"type": "disabled"}},
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-file", type=Path, required=True)
    args = parser.parse_args()
    if args.trace_file.exists():
        raise SystemExit(f"refusing to overwrite trace: {args.trace_file}")

    root = Path(__file__).parents[1]
    case_dir = root / "eval" / "cases" / CASE_ID
    request, _ = load_case(case_dir)
    runtime_state = load_runtime_state(case_dir)
    events = ReplayBackend(case_dir).collect_events(request)
    cluster_events(events)

    audit_executor = RegistryBackedCompactDirectExecutor(
        None, events, runtime_state=runtime_state
    )
    task_prompt = audit_executor._request_prompt(request)
    tool_schema = audit_executor.tools
    implementation_commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    result = RegistryBackedCompactDirectExecutor(
        _model(), events, runtime_state=runtime_state
    ).run(
        request,
        case_id=CASE_ID,
        model_metadata={
            "model": MODEL_NAME,
            "provider": "deepseek",
            "parameters": {
                "temperature": 0,
                "thinking": "disabled",
                "max_tool_rounds": 6,
            },
        },
    )
    trace = result.trace.model_dump(mode="json")
    trace.update(
        {
            "experiment": "registry_backed_real_model_smoke",
            "implementation_commit": implementation_commit,
            "system_prompt": DIRECT_SYSTEM_PROMPT,
            "system_prompt_sha256": _sha256(DIRECT_SYSTEM_PROMPT),
            "task_prompt": task_prompt,
            "task_prompt_sha256": _sha256(task_prompt),
            "task_prompt_bytes": len(task_prompt.encode("utf-8")),
            "tool_names": [item["function"]["name"] for item in tool_schema],
            "tool_schema_sha256": _sha256(_canonical_json(tool_schema)),
            "total_tokens": (trace["input_tokens"] or 0)
            + (trace["output_tokens"] or 0),
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
