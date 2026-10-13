import json

from android_ptc_diag.models import LogEvent
from scripts.eval_real_log_compact_direct import _sanitize_events, _write_reviewed_copy


def test_real_log_model_copy_is_redacted_and_keeps_index_provenance(tmp_path):
    events = [
        LogEvent(
            source="logcat",
            raw_line_number=7,
            pid=4120,
            tid=4120,
            tag="AndroidRuntime",
            message=(
                "loading /data/app/~~secret/com.example.m1crash/base.apk "
                "session 123456789 and 123e4567-e89b-12d3-a456-426614174000"
            ),
            template="loading /data/app/~~secret/<*> session <*>",
        )
    ]

    sanitized, index_map = _sanitize_events(events)
    events_path = tmp_path / "events.jsonl"
    map_path = tmp_path / "map.jsonl"
    _write_reviewed_copy(sanitized, index_map, events_path, map_path)

    saved_event = json.loads(events_path.read_text().strip())
    saved_map = json.loads(map_path.read_text().strip())
    assert saved_event["message"] == (
        "loading <private-path> session <numeric-id> and <uuid>"
    )
    assert saved_event["pid"] == saved_event["tid"] == 1000
    assert saved_event["raw_line_number"] == 7
    assert saved_map["normalized_index"] == 0
    assert saved_map["raw_line_number"] == 7
    assert saved_map["changed"] is True
    assert "secret" not in events_path.read_text()
