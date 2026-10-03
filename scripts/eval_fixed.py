from __future__ import annotations

from pathlib import Path

from android_ptc_diag.backend import ReplayBackend
from android_ptc_diag.cases import load_case
from android_ptc_diag.triage import fixed_triage


def main() -> None:
    cases_root = Path(__file__).parents[1] / "eval" / "cases"
    rows = []

    for case_dir in sorted(path for path in cases_root.iterdir() if path.is_dir()):
        request, expected = load_case(case_dir)
        events = ReplayBackend(case_dir).collect_events(request)
        result = fixed_triage(events, request)
        expected_text = expected["primary_event_contains"]
        ranked_messages = [events[item.event_index].message for item in result.primary_candidates]
        raw_count = len(events)
        candidate_count = len(result.primary_candidates)
        reduction = 1.0 - candidate_count / raw_count if raw_count else 0.0
        rows.append(
            (
                case_dir.name,
                expected_text in ranked_messages[:1],
                any(expected_text in message for message in ranked_messages[:3]),
                raw_count,
                candidate_count,
                reduction,
            )
        )

    print("case                top1  top3  raw  candidates  reduction")
    for name, top1, top3, raw, candidates, reduction in rows:
        print(
            f"{name:<19} {str(top1):<5} {str(top3):<5} "
            f"{raw:>3}  {candidates:>10}  {reduction:>8.1%}"
        )


if __name__ == "__main__":
    main()
