"""Run one registry-backed workflow on a public Android-container incident."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.execution import (
    DIRECT_SYSTEM_PROMPT,
    RegistryBackedCompactDirectExecutor,
)
from android_ptc_diag.models import TriageRequest
from android_ptc_diag.parsing import parse_logcat


MODEL_NAME = "deepseek-v4-pro"
DEFAULT_CASE = "redroid_boot_health_001"
DEFAULT_INCIDENT_ID = "incident-public-android-container-01"

CASE_CONFIGS = {
    "redroid_boot_health_001": {
        "incident_id": DEFAULT_INCIDENT_ID,
        "captured_artifacts": True,
        "request": {
            "symptom": (
                "An Android container was initially unreachable through its expected shell "
                "connection during startup."
            ),
            "diagnosis_scope": "container_runtime_boundary",
            "target_component": "Android container runtime boundary",
            "expected_behavior": "The Android container becomes reachable after startup.",
            "observed_behavior": (
                "The initial connection attempt failed; a crash buffer and separate captured "
                "container/process snapshots are available."
            ),
        },
    },
    "waydroid_hwcomposer_1894": {
        "incident_id": "incident-public-android-container-02",
        "captured_artifacts": False,
        "request": {
            "symptom": "An Android container repeatedly fails to complete startup.",
            "diagnosis_scope": "container_runtime_boundary",
            "target_component": "Android container graphics/runtime boundary",
            "expected_behavior": "The Android container completes startup and becomes usable.",
            "observed_behavior": (
                "The container remains in a startup loop; a bounded Logcat excerpt from one "
                "failure cycle is available."
            ),
        },
    },
}


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


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _process_rows(text: str) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(text.splitlines()[1:], start=2):
        values = line.split(maxsplit=8)
        if len(values) != 9:
            raise ValueError(f"invalid process snapshot row {line_number}")
        user, pid, ppid, vsz, rss, wchan, addr, state, name = values
        rows.append(
            {
                "source_line": line_number,
                "user": user,
                "pid": int(pid),
                "ppid": int(ppid),
                "vsz": int(vsz),
                "rss": int(rss),
                "wchan": wchan,
                "addr": addr,
                "state": state,
                "name": name,
            }
        )
    return rows


def _captured_artifacts(case_dir: Path) -> list[dict[str, Any]]:
    inspect_path = case_dir / "container_inspect.json"
    process_path = case_dir / "process_snapshot.txt"
    container = json.loads(inspect_path.read_text(encoding="utf-8"))[0]
    state = container["State"]
    process_text = process_path.read_text(encoding="utf-8")
    return [
        {
            "artifact_id": "captured-container-inspect",
            "subject_type": "container",
            "subject_id": "runtime-under-review",
            "observations": {
                "state": {
                    field: state[field]
                    for field in (
                        "Status",
                        "Running",
                        "Paused",
                        "Restarting",
                        "OOMKilled",
                        "Dead",
                        "Pid",
                        "ExitCode",
                        "Error",
                        "StartedAt",
                        "FinishedAt",
                    )
                }
            },
            "sha256": _sha256_bytes(inspect_path.read_bytes()),
        },
        {
            "artifact_id": "captured-process-snapshot",
            "subject_type": "container",
            "subject_id": "runtime-under-review",
            "observations": {"process_rows": _process_rows(process_text)},
            "sha256": _sha256_bytes(process_path.read_bytes()),
        },
    ]


class PublicIncidentExecutor(RegistryBackedCompactDirectExecutor):
    """Evaluation-only adapter for captured state that has no replay inventory."""

    def __init__(self, *args: Any, captured_artifacts: list[dict[str, Any]], **kwargs: Any):
        super().__init__(
            *args,
            reliability_hardening=True,
            deduplicated_evidence_view=True,
            **kwargs,
        )
        self.captured_artifacts = captured_artifacts

    def _request_prompt(self, request: TriageRequest) -> str:
        base_prompt = super()._request_prompt(request)
        instructions, serialized = base_prompt.rsplit("\n", 1)
        payload = json.loads(serialized)
        visible_by_id = {}
        for artifact in self.captured_artifacts:
            items = self.evidence_registry.add_initial_artifact(
                artifact_id=artifact["artifact_id"],
                subject_type=artifact["subject_type"],
                subject_id=artifact["subject_id"],
                observations=artifact["observations"],
            )
            for item in items:
                visible_by_id[item.evidence_id] = item
        visible = self.evidence_registry.model_items(visible_by_id.values())
        payload.update(
            {
                "diagnosis_scope": request.diagnosis_scope,
                "target_component": request.target_component,
                "expected_behavior": request.expected_behavior,
                "observed_behavior": request.observed_behavior,
            }
        )
        if visible:
            payload.update(
                {
                    "captured_runtime_evidence": visible,
                    "captured_state_semantics": (
                        "The container inspect and process-list artifacts are separate "
                        "captured observations. Their exact collection time is not encoded, "
                        "so do not infer a strict order between them and Logcat."
                    ),
                }
            )
        return instructions + "\n" + json.dumps(payload, ensure_ascii=False)


def prepare_evaluation(root: Path, case_name: str = DEFAULT_CASE) -> dict[str, Any]:
    config = CASE_CONFIGS[case_name]
    case_dir = (
        root
        / "eval"
        / "public_android_container_incidents"
        / case_name
    )
    events = parse_logcat((case_dir / "logcat.txt").read_text(encoding="utf-8"))
    cluster_events(events)
    artifacts = _captured_artifacts(case_dir) if config["captured_artifacts"] else []
    request = TriageRequest(**config["request"])
    executor = PublicIncidentExecutor(None, events, captured_artifacts=artifacts)
    task_prompt = executor._request_prompt(request)
    return {
        "case_dir": case_dir,
        "case_name": case_name,
        "default_incident_id": config["incident_id"],
        "events": events,
        "captured_artifacts": artifacts,
        "request": request,
        "task_prompt": task_prompt,
        "tool_schema": executor.tools,
        "evidence_registry": executor.evidence_registry.snapshot(),
        "evidence_handle_map": executor.evidence_registry.handle_map(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(CASE_CONFIGS), default=DEFAULT_CASE)
    parser.add_argument("--incident-id")
    parser.add_argument("--implementation-commit")
    parser.add_argument("--trace-file", type=Path, required=True)
    args = parser.parse_args()
    if args.trace_file.exists():
        raise SystemExit(f"refusing to overwrite trace: {args.trace_file}")

    root = Path(__file__).parents[1]
    prepared = prepare_evaluation(root, args.case)
    incident_id = args.incident_id or prepared["default_incident_id"]
    repository_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    implementation_commit = args.implementation_commit or repository_commit
    result = PublicIncidentExecutor(
        _model(),
        prepared["events"],
        captured_artifacts=prepared["captured_artifacts"],
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
            "experiment": "public_android_container_registry_smoke",
            "neutral_incident_id": incident_id,
            "evaluation_case": args.case,
            "implementation_commit": implementation_commit,
            "repository_commit": repository_commit,
            "input_artifacts": {
                "logcat": _sha256_bytes((prepared["case_dir"] / "logcat.txt").read_bytes()),
                **{
                    artifact["artifact_id"]: artifact["sha256"]
                    for artifact in prepared["captured_artifacts"]
                },
            },
            "system_prompt": DIRECT_SYSTEM_PROMPT,
            "system_prompt_sha256": _sha256_text(DIRECT_SYSTEM_PROMPT),
            "task_prompt": prepared["task_prompt"],
            "task_prompt_sha256": _sha256_text(prepared["task_prompt"]),
            "task_prompt_bytes": len(prepared["task_prompt"].encode("utf-8")),
            "tool_names": [
                item["function"]["name"] for item in prepared["tool_schema"]
            ],
            "tool_schema_sha256": _sha256_text(
                _canonical_json(prepared["tool_schema"])
            ),
            "provider_retry_policy": {"max_retries": 0},
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
