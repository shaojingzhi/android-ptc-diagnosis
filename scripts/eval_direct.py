"""Run the bounded DIRECT baseline against replay cases with OpenAI ChatGPT."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from android_ptc_diag.backend import ReplayBackend
from android_ptc_diag.cases import load_case
from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.execution import DirectExecutor


def _build_model(model_name: str, base_url: str | None) -> Any:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required for the real DIRECT smoke run")
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise SystemExit('Install the optional DIRECT dependency with: pip install -e ".[direct]"') from exc

    kwargs = {"model": model_name, "temperature": 0}
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def _case_dirs(root: Path, selected: str | None) -> list[Path]:
    if selected:
        path = root / selected
        if not path.is_dir():
            raise SystemExit(f"unknown replay case: {selected}")
        return [path]
    return sorted(path for path in root.iterdir() if path.is_dir())


def _write_trace(path: Path, trace: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(trace, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M3a DIRECT replay smoke test")
    parser.add_argument("--case", help="one case directory name; default: all replay cases")
    parser.add_argument("--model", default=os.environ.get("DIRECT_MODEL", "gpt-4o-mini"))
    parser.add_argument("--base-url", default=os.environ.get("DIRECT_BASE_URL"))
    parser.add_argument("--trace-file", type=Path, help="optional JSONL trace output path")
    args = parser.parse_args()

    model = _build_model(args.model, args.base_url)
    cases_root = Path(__file__).parents[1] / "eval" / "cases"
    rows = []
    for case_dir in _case_dirs(cases_root, args.case):
        request, expected = load_case(case_dir)
        events = ReplayBackend(case_dir).collect_events(request)
        cluster_events(events)
        result = DirectExecutor(model, events).run(
            request,
            case_id=case_dir.name,
            model_metadata={"model": args.model, "provider": "openai"},
        )
        diagnosis = result.diagnosis
        primary = diagnosis.primary_candidate_indices if diagnosis else []
        expected_text = expected["primary_event_contains"]
        messages = [events[index].message for index in primary if 0 <= index < len(events)]
        rows.append(
            (
                case_dir.name,
                bool(messages and expected_text in messages[0]),
                any(expected_text in message for message in messages[:3]),
                result.trace,
            )
        )
        if args.trace_file:
            _write_trace(args.trace_file, result.trace.model_dump())

    print("case                top1  top3  turns  tools  tokens  latency_ms  errors")
    for name, top1, top3, trace in rows:
        tokens = None
        if trace.input_tokens is not None or trace.output_tokens is not None:
            tokens = (trace.input_tokens or 0) + (trace.output_tokens or 0)
        print(
            f"{name:<19} {str(top1):<5} {str(top3):<5} {trace.model_turns:>6} "
            f"{trace.tool_calls:>6} {str(tokens):>7} {trace.total_latency_ms:>11.1f} "
            f"{len(trace.errors):>6}"
        )


if __name__ == "__main__":
    main()
