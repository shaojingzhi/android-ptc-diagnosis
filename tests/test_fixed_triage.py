from pathlib import Path

from android_ptc_diag.backend import ReplayBackend
from android_ptc_diag.cases import load_case
from android_ptc_diag.models import LogEvent, TriageRequest
from android_ptc_diag.triage import fixed_triage


CASES_ROOT = Path(__file__).parents[1] / "eval" / "cases"


def test_drain3_clusters_duplicate_binder_events():
    case_dir = CASES_ROOT / "noisy_root_001"
    request, _ = load_case(case_dir)
    events = ReplayBackend(case_dir).collect_events(request)

    result = fixed_triage(events, request)
    binder_indices = [
        index for index, event in enumerate(events) if "binder transaction failed" in event.message
    ]

    assert len(binder_indices) == 3
    assert len({events[index].template_id for index in binder_indices}) == 1
    assert events[binder_indices[0]].template
    assert {events[index].repeat_count for index in binder_indices} == {3}
    assert binder_indices[0] in result.evidence_event_indices
    assert binder_indices[0] in [candidate.event_index for candidate in result.primary_candidates]
    assert set(binder_indices[1:]) <= set(result.suppressed_duplicate_indices)
    assert result.noise_event_indices == []


def test_seed_primary_events_reach_top_three():
    for case_dir in sorted(path for path in CASES_ROOT.iterdir() if path.is_dir()):
        request, expected = load_case(case_dir)
        events = ReplayBackend(case_dir).collect_events(request)
        result = fixed_triage(events, request)
        top_messages = [events[item.event_index].message for item in result.primary_candidates[:3]]

        assert any(expected["primary_event_contains"] in message for message in top_messages), case_dir.name


def test_fixed_triage_scores_are_valid_and_stable():
    events = [
        LogEvent(source="logcat", level="E", message="service failed id=1"),
        LogEvent(source="logcat", level="E", message="service failed id=2"),
        LogEvent(source="logcat", level="W", message="unrelated warning"),
    ]
    request = TriageRequest(symptom="service failure")

    first = fixed_triage(events, request)
    first_order = [candidate.event_index for candidate in first.primary_candidates]
    second = fixed_triage(events, request)

    assert all(0.0 <= candidate.score <= 1.0 for candidate in first.primary_candidates)
    assert first_order == [candidate.event_index for candidate in second.primary_candidates]
    assert first.primary_candidates[0].reason
    assert events[0].repeat_count == 2
