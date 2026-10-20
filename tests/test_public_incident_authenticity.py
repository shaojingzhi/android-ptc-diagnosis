from hashlib import sha256
from pathlib import Path

import yaml

from android_ptc_diag.backend import ReplayBackend
from android_ptc_diag.cases import load_case, load_case_metadata, load_runtime_state
from android_ptc_diag.clustering import cluster_events
from android_ptc_diag.compact import build_compact_evidence
from android_ptc_diag.primitives import filter_by_text, get_events


CASES = Path(__file__).parents[1] / "eval" / "public_incidents"
PUBLIC_CASES = {
    "public_containerd_overlay_001": "logcat.txt",
    "public_runc_memcg_001": "kmsg.txt",
    "public_containerd_shim_001": "logcat.txt",
}
EXPECTED_SHAPES = {
    "public_containerd_overlay_001": (1, 1),
    "public_runc_memcg_001": (5, 5),
    "public_containerd_shim_001": (1, 1),
}


def _case(name: str) -> Path:
    return CASES / name


def _nonempty_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_public_incident_source_ledgers_pin_unchanged_excerpts():
    for name, model_file in PUBLIC_CASES.items():
        case_dir = _case(name)
        source = yaml.safe_load((case_dir / "source.yaml").read_text(encoding="utf-8"))
        excerpt_bytes = (case_dir / source["artifact"]["file"]).read_bytes()

        assert load_case_metadata(case_dir).provenance == "public_case_adapted"
        assert source["source_kind"].startswith("upstream_")
        assert source["url"].startswith("https://github.com/")
        assert source["artifact"]["content_transform"] == "none"
        assert source["artifact"]["model_visible_copy"] == model_file
        assert sha256(excerpt_bytes).hexdigest() == source["artifact"]["sha256"]
        assert (case_dir / model_file).read_bytes() == excerpt_bytes
        assert "issue text" in source["license_note"]
        assert source["supports"]
        assert source["not_claimed"]


def test_public_incident_excerpts_enter_replay_without_rewriting_lines():
    for name, model_file in PUBLIC_CASES.items():
        case_dir = _case(name)
        request, _ = load_case(case_dir)
        events = ReplayBackend(case_dir).collect_events(request)
        source_lines = _nonempty_lines(case_dir / model_file)

        assert [event.message for event in events] == source_lines
        assert [event.raw_line_number for event in events] == list(
            range(1, len(source_lines) + 1)
        )
        assert all(event.timestamp is None for event in events)
        assert all(event.monotonic_seconds is None for event in events)
        assert all(event.tag is None and event.level is None for event in events)


def test_public_incident_evidence_remains_reachable_and_traceable_after_compact():
    for name in PUBLIC_CASES:
        case_dir = _case(name)
        request, expected = load_case(case_dir)
        events = ReplayBackend(case_dir).collect_events(request)

        matches = filter_by_text(events, text=expected["primary_event_contains"])
        assert matches["count"] >= 1
        materialized = get_events(events, indices=matches["indices"])
        assert [record["index"] for record in materialized["events"]] == matches["indices"]
        assert all(events[index].raw_line_number is not None for index in matches["indices"])

        cluster_events(events)
        compact = build_compact_evidence(events)
        assert (len(events), compact["group_count"]) == EXPECTED_SHAPES[name]
        grouped_indices = sorted(
            index for group in compact["groups"] for index in group["indices"]
        )
        assert grouped_indices == list(range(len(events)))
        assert all(group["source"] in {"logcat", "kmsg"} for group in compact["groups"])


def test_public_incident_model_input_excludes_source_analysis_and_hidden_evaluation():
    for name in PUBLIC_CASES:
        case_dir = _case(name)
        case_yaml = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))
        visible = "\n".join(
            (case_dir / filename).read_text(encoding="utf-8")
            for filename in ("case.yaml", "logcat.txt", "kmsg.txt")
            if (case_dir / filename).exists()
        )

        assert case_yaml["metadata"]["model_incident_id"].startswith("public-incident-")
        assert "github.com" not in visible
        assert "allowed_conclusion" not in visible
        assert "unsupported_claims" not in visible
        assert load_runtime_state(case_dir) == {}
