from pathlib import Path

import yaml

from android_ptc_diag.backend import ReplayBackend
from android_ptc_diag.cases import load_case, load_case_metadata, load_runtime_state
from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.compact import build_compact_evidence, compact_model_view
from android_ptc_diag.primitives import (
    filter_by_text,
    get_events,
    inspect_cgroup_state,
    inspect_mount_state,
    inspect_process_state,
    inspect_service_state,
)
from android_ptc_diag.skills import build_evidence_view


CASES = Path(__file__).parents[1] / "eval" / "cases"
PAIRS = {
    "startup_mount_config": "synthetic",
    "startup_cgroup_oom": "synthetic",
    "startup_dependency_unready": "synthetic",
    "startup_userns_limit": "public_case_adapted",
}
EXPECTED_SHAPES = {
    "startup_mount_config_full": (12, 10),
    "startup_mount_config_limited": (10, 8),
    "startup_cgroup_oom_full": (14, 12),
    "startup_cgroup_oom_limited": (10, 8),
    "startup_dependency_unready_full": (11, 9),
    "startup_dependency_unready_limited": (9, 7),
    "startup_userns_limit_full": (12, 10),
    "startup_userns_limit_limited": (9, 7),
}


def _case(pair: str, version: str) -> Path:
    return CASES / f"{pair}_{version}"


def _visible_text(case_dir: Path) -> str:
    names = ("case.yaml", "logcat.txt", "kmsg.txt", "runtime_state.yaml")
    return "\n".join(
        (case_dir / name).read_text(encoding="utf-8")
        for name in names
        if (case_dir / name).exists()
    )


def test_paired_cases_separate_model_input_from_evaluation_and_load_repeatably():
    for pair, provenance in PAIRS.items():
        full_dir = _case(pair, "full")
        limited_dir = _case(pair, "limited")
        full_request, full_expected = load_case(full_dir)
        limited_request, limited_expected = load_case(limited_dir)

        assert full_request.symptom == limited_request.symptom
        assert full_request.expected_behavior == limited_request.expected_behavior
        assert full_request.observed_behavior == limited_request.observed_behavior
        assert load_case(full_dir) == (full_request, full_expected)
        assert load_case(limited_dir) == (limited_request, limited_expected)
        assert load_case_metadata(full_dir).provenance == provenance
        assert load_case_metadata(limited_dir).provenance == provenance

        for case_dir, version in ((full_dir, "full"), (limited_dir, "limited")):
            case_yaml = yaml.safe_load(
                (case_dir / "case.yaml").read_text(encoding="utf-8")
            )
            assert "expected" not in case_yaml
            assert case_yaml["metadata"]["model_incident_id"].startswith(
                "startup-incident-"
            )
            assert case_yaml["metadata"]["evidence_version"] == (
                "complete" if version == "full" else "limited"
            )
            visible = _visible_text(case_dir).casefold()
            for forbidden in ("root_cause", "expected_owner", "candidate_ranking"):
                assert forbidden not in visible


def test_startup_cases_preserve_parser_provenance_and_compact_identity():
    for pair in PAIRS:
        for version in ("full", "limited"):
            case_dir = _case(pair, version)
            request, expected = load_case(case_dir)
            events = ReplayBackend(case_dir).collect_events(request)

            assert events
            assert any(
                expected["primary_event_contains"] in event.message for event in events
            )
            assert all(event.raw_line_number is not None for event in events)
            cluster_events(events)
            compact = build_compact_evidence(events)
            assert (len(events), compact["group_count"]) == EXPECTED_SHAPES[
                case_dir.name
            ]
            grouped_indices = sorted(
                index
                for group in compact["groups"]
                for index in group["indices"]
            )
            assert grouped_indices == list(range(len(events)))

            fresh_events = ReplayBackend(case_dir).collect_events(request)
            cluster_events(fresh_events)
            assert compact_model_view(build_compact_evidence(fresh_events)) == (
                compact_model_view(compact)
            )


def test_complete_versions_keep_key_evidence_reachable_through_existing_tools():
    for pair in PAIRS:
        case_dir = _case(pair, "full")
        request, expected = load_case(case_dir)
        events = ReplayBackend(case_dir).collect_events(request)
        cluster_events(events)

        matches = filter_by_text(
            events, text=expected["distinguishing_event_contains"]
        )
        assert matches["count"] >= 1
        records = get_events(events, indices=matches["indices"])
        assert all("index" in record for record in records["events"])
        compact_text = " ".join(
            group["template"] for group in build_compact_evidence(events)["groups"]
        )
        assert expected["distinguishing_event_contains"] in compact_text


def test_limited_versions_do_not_leak_removed_evidence():
    for pair in PAIRS:
        case_dir = _case(pair, "limited")
        _, expected = load_case(case_dir)
        visible = _visible_text(case_dir).casefold()

        assert "not_collected" in visible or "collection unavailable" in visible
        for marker in expected["removed_evidence_markers"]:
            assert marker.casefold() not in visible
        assert expected["required_unknowns"]
        assert expected["best_next_check"]


def test_complete_runtime_state_matches_pair_mechanisms_without_new_tools():
    mount_state = load_runtime_state(_case("startup_mount_config", "full"))
    upper = inspect_mount_state(mount_state, path="/state/upper")
    work = inspect_mount_state(mount_state, path="/scratch/work")
    init = inspect_process_state(mount_state, name="container_init")
    assert upper["mount_id"] != work["mount_id"]
    assert init["state"] == "not_started"

    oom_state = load_runtime_state(_case("startup_cgroup_oom", "full"))
    cgroup = inspect_cgroup_state(oom_state, name="runtime_init")
    process = inspect_process_state(oom_state, name="runtime_init")
    assert cgroup["memory_events_local_before"]["oom_kill"] == 0
    assert cgroup["memory_events_local_after"]["oom_kill"] == 1
    assert cgroup["host_mem_available_bytes"] > cgroup["memory_max_bytes"]
    assert process["cgroup_path"] == cgroup["path"]

    dependency_state = load_runtime_state(
        _case("startup_dependency_unready", "full")
    )
    caller = inspect_service_state(dependency_state, name="runtime_service")
    dependency = inspect_service_state(dependency_state, name="host_bridge")
    assert caller["last_connect_result"] == "ECONNREFUSED"
    assert dependency["ready"] is False
    assert dependency["listener_bound"] is False


def test_mixed_source_complete_cases_do_not_claim_global_time_order():
    for pair in ("startup_mount_config", "startup_cgroup_oom"):
        case_dir = _case(pair, "full")
        request, _ = load_case(case_dir)
        events = ReplayBackend(case_dir).collect_events(request)
        view = build_evidence_view(get_events(events)["events"])

        assert set(view["sources"]) == {"logcat", "kmessage"}
        assert view["cross_source_order"] == "unknown"
        assert view["ordering"] == "source_local"
