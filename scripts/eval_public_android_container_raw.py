"""Run the one-call RAW ablation for the Waydroid public incident."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

from android_ptc_diag.execution import _content, _parse_json_payload, _redact_text, _usage
from android_ptc_diag.models import DirectDiagnosis
from android_ptc_diag.parsing import parse_logcat


MODEL_NAME = "deepseek-v4-pro"
RAW_SYSTEM_PROMPT = """You are diagnosing a noisy Android/container startup incident.
You have no tools and must use only the raw Logcat records in the user message.
Identify a primary failure candidate, supporting evidence, propagation candidates,
uncertainties, and read-only recommended next checks. Do not claim proven causality
or a proven root cause. Return only JSON with exactly these fields:
primary_candidate_indices, evidence_indices, propagation_event_indices,
uncertainties, recommended_next_checks, notes.
The indices refer to the explicit normalized event index prefix on each raw record.
"""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


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


def _task(raw_lines: list[str]) -> str:
    records = "\n".join(
        f"[normalized_event_index={index}] {line}"
        for index, line in enumerate(raw_lines)
    )
    return (
        "Incident ID: incident-public-android-container-02\n"
        "Symptom: An Android container repeatedly fails to complete startup.\n"
        "Diagnosis scope: container_runtime_boundary.\n"
        "Expected behavior: the container completes startup and becomes usable.\n"
        "Observed behavior: the container remains in a startup loop.\n\n"
        "Raw Logcat records (the text after each index is unchanged):\n"
        + records
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-file", type=Path, required=True)
    parser.add_argument("--implementation-commit")
    args = parser.parse_args()
    if args.trace_file.exists():
        raise SystemExit(f"refusing to overwrite trace: {args.trace_file}")

    root = Path(__file__).parents[1]
    case_path = root / "eval/public_android_container_incidents/waydroid_hwcomposer_1894/logcat.txt"
    raw_bytes = case_path.read_bytes()
    events = parse_logcat(raw_bytes.decode("utf-8"))
    task = _task(raw_bytes.decode("utf-8").splitlines())
    repository_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    started = time.perf_counter()
    response = _model().invoke(
        [
            {"role": "system", "content": RAW_SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]
    )
    latency_ms = (time.perf_counter() - started) * 1000
    raw_final = _redact_text(_content(response))
    parsed: dict[str, Any] | None = None
    validation_errors: list[str] = []
    try:
        payload = _parse_json_payload(raw_final)
        parsed = DirectDiagnosis.model_validate(payload).model_dump(mode="json")
        parsed_diagnosis = DirectDiagnosis.model_validate(payload)
        parsed_diagnosis.validate_event_indices(len(events))
    except Exception as exc:  # noqa: BLE001 - preserve one-call ablation failures
        validation_errors.append(_redact_text(str(exc)))
    input_tokens, output_tokens = _usage(response)
    trace = {
        "experiment": "public_android_container_raw_ablation",
        "neutral_incident_id": "incident-public-android-container-02",
        "implementation_commit": args.implementation_commit or repository_commit,
        "repository_commit": repository_commit,
        "model": MODEL_NAME,
        "provider": "deepseek",
        "model_parameters": {
            "temperature": 0,
            "thinking": "disabled",
            "provider_max_retries": 0,
        },
        "input_artifacts": {
            "logcat_excerpt_sha256": _sha256_bytes(raw_bytes),
            "logcat_excerpt_bytes": len(raw_bytes),
            "normalized_event_count": len(events),
        },
        "system_prompt_sha256": _sha256_text(RAW_SYSTEM_PROMPT),
        "system_prompt_bytes": len(RAW_SYSTEM_PROMPT.encode("utf-8")),
        "task_prompt_sha256": _sha256_text(task),
        "task_prompt_bytes": len(task.encode("utf-8")),
        "model_turns": 1,
        "tool_calls": 0,
        "tool_calling_rounds": 0,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        ),
        "total_latency_ms": latency_ms,
        "final_status": "accepted" if parsed is not None and not validation_errors else "rejected",
        "final_raw_text": raw_final,
        "final_parsed_payload": parsed,
        "final_validation_errors": validation_errors,
        "stop_reason": None if parsed is not None and not validation_errors else "invalid final",
        "tool_names": [],
    }
    args.trace_file.parent.mkdir(parents=True, exist_ok=True)
    args.trace_file.write_text(json.dumps(trace, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: trace[key] for key in (
        "final_status", "model_turns", "tool_calls", "input_tokens", "output_tokens",
        "total_tokens", "total_latency_ms", "final_validation_errors",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
