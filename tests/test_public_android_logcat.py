from hashlib import sha256
from pathlib import Path

import yaml

from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.compact import build_compact_evidence
from android_ptc_diag.parsing import parse_logcat
from android_ptc_diag.primitives import filter_by_text, get_events


DATA = Path(__file__).parents[1] / "eval" / "public_logcat"
EXPECTED = {
    "loghub_android_v1_2k": {
        "events": 2000,
        "groups": 164,
        "levels": {"D": 650, "E": 3, "I": 920, "V": 257, "W": 170},
    },
    "qfield_native_crash_excerpt": {
        "events": 45,
        "groups": 38,
        "levels": {"D": 1, "E": 6, "F": 18, "I": 15, "W": 5},
    },
}


def _load(name: str):
    case_dir = DATA / name
    source = yaml.safe_load((case_dir / "source.yaml").read_text(encoding="utf-8"))
    raw = (case_dir / source["artifact"]["file"]).read_bytes()
    return source, raw, parse_logcat(raw.decode("utf-8"))


def test_public_android_artifacts_are_hash_pinned_real_logcat():
    for name, expected in EXPECTED.items():
        source, raw, events = _load(name)

        assert source["provenance"] in {
            "public_dataset_real_logcat",
            "public_incident_real_logcat_excerpt",
        }
        assert source["format"] == "android_logcat_threadtime"
        assert sha256(raw).hexdigest() == source["artifact"]["sha256"]
        assert len(raw) == source["artifact"]["bytes"]
        assert len(raw.splitlines()) == source["artifact"]["lines"]
        assert len(events) == expected["events"]
        assert all(event.timestamp is not None for event in events)
        assert [event.raw_line_number for event in events] == list(
            range(1, len(events) + 1)
        )


def test_public_android_logs_keep_level_component_and_group_provenance():
    for name, expected in EXPECTED.items():
        _, _, events = _load(name)
        levels = {
            level: sum(event.level == level for event in events)
            for level in sorted({event.level for event in events})
        }
        assert levels == expected["levels"]

        cluster_events(events)
        compact = build_compact_evidence(events)
        assert compact["event_count"] == expected["events"]
        assert compact["group_count"] == expected["groups"]
        grouped_indices = [
            index for group in compact["groups"] for index in group["indices"]
        ]
        assert sorted(grouped_indices) == list(range(len(events)))
        assert len(grouped_indices) == len(set(grouped_indices))
        assert all(group["source"] == "logcat" for group in compact["groups"])
        assert all(group["tag"] and group["level"] for group in compact["groups"])


def test_qfield_real_crash_evidence_is_bounded_and_reachable():
    _, _, events = _load("qfield_native_crash_excerpt")

    fatal = filter_by_text(events, text="Fatal signal 11 (SIGSEGV)")
    cause = filter_by_text(events, text="Cause: null pointer dereference")
    frame = filter_by_text(events, text="FeatureModel::applyGeometry")
    death = filter_by_text(events, text="Process ch.opengis.qfield_beta")

    assert fatal["indices"] == [5]
    assert cause["indices"] == [17]
    assert frame["indices"] == [24]
    assert death["indices"] == [43]
    materialized = get_events(
        events,
        indices=fatal["indices"] + cause["indices"] + frame["indices"] + death["indices"],
    )
    assert materialized["count"] == 4
    assert [record["index"] for record in materialized["events"]] == [5, 17, 24, 43]


def test_public_logcat_ledgers_do_not_claim_hidden_diagnosis_labels():
    forbidden = {"root_cause", "expected", "ground_truth", "allowed_conclusion"}
    for name in EXPECTED:
        source, _, _ = _load(name)
        assert forbidden.isdisjoint(source)
        assert source["supports"]
        assert source["not_claimed"]

