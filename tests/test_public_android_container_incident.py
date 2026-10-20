from hashlib import sha256
import json
from pathlib import Path
import re

import yaml

from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.compact import build_compact_evidence
from android_ptc_diag.models import TriageRequest
from android_ptc_diag.parsing import parse_logcat
from android_ptc_diag.primitives import filter_by_text, get_events
from android_ptc_diag.triage import fixed_triage


INCIDENT = (
    Path(__file__).parents[1]
    / "eval"
    / "public_android_container_incidents"
    / "redroid_boot_health_001"
)


def _source() -> dict:
    return yaml.safe_load((INCIDENT / "source.yaml").read_text(encoding="utf-8"))


def test_redroid_public_artifacts_are_byte_exact_and_hash_pinned():
    source = _source()

    assert source["provenance"] == "public_android_container_incident"
    assert source["source_archive"]["actual_format"] == "tar.gz"
    for artifact in source["preserved_files"]:
        raw = (INCIDENT / artifact["local_file"]).read_bytes()
        assert artifact["content_transform"] == "rename_only"
        assert sha256(raw).hexdigest() == artifact["sha256"]
        assert len(raw) == artifact["bytes"]
        assert len(raw.splitlines()) == artifact["lines"]


def test_redroid_crash_buffer_parses_with_original_line_provenance():
    events = parse_logcat((INCIDENT / "logcat.txt").read_text(encoding="utf-8"))

    assert len(events) == 75
    assert events[0].raw_line_number == 2
    assert events[-1].raw_line_number == 76
    assert all(event.timestamp is not None for event in events)
    assert sum(event.level == "F" for event in events) == 69
    assert sum(event.level == "E" for event in events) == 6

    cluster_events(events)
    compact = build_compact_evidence(events)
    assert compact["group_count"] == 45
    grouped = [index for group in compact["groups"] for index in group["indices"]]
    assert sorted(grouped) == list(range(75))
    assert len(grouped) == len(set(grouped))


def test_redroid_crash_and_propagation_evidence_remain_reachable():
    events = parse_logcat((INCIDENT / "logcat.txt").read_text(encoding="utf-8"))
    expected = {
        "Fatal signal 11": [0],
        "memtrack_proc_new": [15],
        "DeadSystemException": [31, 34],
        "Fatal signal 6": [36],
    }

    for text, indices in expected.items():
        matches = filter_by_text(events, text=text)
        assert matches["indices"] == indices
        materialized = get_events(events, indices=indices)
        assert [record["index"] for record in materialized["events"]] == indices

    triage = fixed_triage(
        events,
        TriageRequest(
            symptom="redroid connection initially unavailable",
            target_component="container runtime",
        ),
    )
    assert triage.primary_candidates[0].event_index == 0


def test_redroid_state_evidence_supports_boot_health_without_erasing_crash():
    container = json.loads((INCIDENT / "container_inspect.json").read_text())[0]
    processes = (INCIDENT / "process_snapshot.txt").read_text(encoding="utf-8")
    events = parse_logcat((INCIDENT / "logcat.txt").read_text(encoding="utf-8"))

    assert container["State"]["Status"] == "running"
    assert container["State"]["Running"] is True
    assert container["State"]["OOMKilled"] is False
    assert container["State"]["ExitCode"] == 0
    assert re.search(r"^system\s+1527\s+1432.*\ssystem_server$", processes, re.MULTILINE)
    assert re.search(r"^shell\s+83\s+1.*\sadbd$", processes, re.MULTILINE)
    assert "pid 251 (system_server)" in events[0].message


def test_redroid_assessment_keeps_evidence_and_claim_boundaries_separate():
    assessment = yaml.safe_load((INCIDENT / "assessment.yaml").read_text())

    assert assessment["model_visible"] is False
    assert assessment["supported_conclusions"]
    assert assessment["uncertainties"]
    assert assessment["unsupported_claims"]
    assert not (INCIDENT / "runtime_state.yaml").exists()
    assert not (INCIDENT / "expected.yaml").exists()
    assert "root_cause" not in assessment
