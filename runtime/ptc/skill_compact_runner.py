"""Run the skill-bounded compact PTC mode through the shared runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from android_ptc_diag.compact import build_compact_evidence, compact_model_view  # noqa: E402
from android_ptc_diag.execution import DIRECT_SYSTEM_PROMPT  # noqa: E402
from android_ptc_diag.models import LogEvent  # noqa: E402
from runtime.ptc import runner  # noqa: E402

SKILL_COMPACT_PTC_SYSTEM_SUFFIX = DIRECT_SYSTEM_PROMPT + """
The compact evidence is the primary index over the raw log events.
Two tested reusable skills from the source module android_ptc_diag.skills are
available in generated code from tools.android_ptc_diag.skills:

from tools.android_ptc_diag.skills import (
    select_group_evidence_indices,
    build_timeline,
)

Use compact groups to decide which evidence requires inspection. Prefer these
skills over reimplementing boundary selection, deduplication, or timeline order.
The controlled pattern is:

indices = select_group_evidence_indices(suspect_groups, raw_event_count,
                                        boundary_radius=1)
raw = get_events(indices=indices)["events"]
timeline = build_timeline(raw)

Lightweight loops, conditions, filters, joins, and aggregation should remain in
generated code. Return only compact evidence needed for diagnosis. Compact
evidence is not proof of causality. Do not read protected snapshot or runtime
files directly. Avoid get_events() without explicit indices unless compact
evidence is genuinely insufficient. Prefer as few execute_code programs as
practical. Other diagnostic primitives remain available when useful."""


def _build_skill_compact_task(request: Any, events: list[LogEvent]) -> str:
    compact = compact_model_view(build_compact_evidence(events))
    return json.dumps(
        {
            "symptom": request.symptom,
            "target_package": request.target_package,
            "failure_timestamp": request.failure_timestamp.isoformat()
            if request.failure_timestamp
            else None,
            "raw_event_count": len(events),
            "compact_group_count": compact["group_count"],
            "compact_evidence": compact,
            "instructions": (
                "Use compact evidence to select groups, compose the tested skills "
                "with diagnostic primitives in generated code, and return diagnosis JSON."
            ),
            "output_schema": {
                "primary_candidate_indices": "list[int]",
                "evidence_indices": "list[int]",
                "propagation_event_indices": "list[int]",
                "notes": "list[str]",
            },
        },
        ensure_ascii=False,
    )


async def run_case(case_dir: Path, trace_file: Path) -> dict[str, Any]:
    return await runner._run_case(
        case_dir,
        trace_file,
        mode="skill_compact_ptc",
        task_builder=_build_skill_compact_task,
        system_prompt_suffix=SKILL_COMPACT_PTC_SYSTEM_SUFFIX,
        extra_shared_modules=("skills.py",),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run skill-bounded compact PTC")
    parser.add_argument("--case", required=True)
    parser.add_argument("--trace-file", type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get("DAYTONA_API_KEY"):
        raise SystemExit("DAYTONA_API_KEY is required for a real PTC run")
    case_dir = REPO_ROOT / "eval" / "cases" / args.case
    if not case_dir.is_dir():
        raise SystemExit("unknown replay case")
    args.trace_file.parent.mkdir(parents=True, exist_ok=True)
    runner._print_row(await run_case(case_dir, args.trace_file))


if __name__ == "__main__":
    asyncio.run(main())
