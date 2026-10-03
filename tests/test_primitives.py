from datetime import datetime

from android_ptc_diag.models import LogEvent
from android_ptc_diag.primitives import (
    filter_by_level,
    filter_by_tag,
    filter_by_text,
    filter_by_time,
    first_occurrence,
    get_events,
    group_by_pid,
    group_by_template,
    tool_definitions,
)


def _events() -> list[LogEvent]:
    return [
        LogEvent(
            source="logcat",
            timestamp=datetime(2026, 10, 3, 10, 0, 0),
            level="I",
            pid=10,
            tag="ActivityManager",
            message="service started id=1",
            template_id="1",
            template="service started id=<*>",
        ),
        LogEvent(
            source="logcat",
            timestamp=datetime(2026, 10, 3, 10, 0, 1),
            level="E",
            pid=10,
            tag="AndroidRuntime",
            message="FATAL EXCEPTION: main",
            template_id="2",
            template="FATAL EXCEPTION: main",
        ),
        LogEvent(
            source="logcat",
            timestamp=datetime(2026, 10, 3, 10, 0, 2),
            level="E",
            pid=11,
            tag="AndroidRuntime",
            message="FATAL EXCEPTION: main",
            template_id="2",
            template="FATAL EXCEPTION: main",
        ),
    ]


def test_filters_return_indices_and_support_chaining():
    events = _events()

    errors = filter_by_level(events, levels=["e"])
    assert errors == {"indices": [1, 2], "count": 2}
    assert filter_by_text(events, indices=errors["indices"], text="fatal") == errors
    assert filter_by_tag(events, tags=["AndroidRuntime"]) == errors
    assert filter_by_time(events, start="2026-10-03T10:00:01", end="2026-10-03T10:00:01") == {
        "indices": [1],
        "count": 1,
    }


def test_grouping_preserves_original_indices():
    events = _events()

    templates = group_by_template(events)
    assert templates["count"] == 2
    assert templates["groups"][1]["indices"] == [1, 2]
    assert first_occurrence(events) == {"indices": [0, 1], "count": 2}
    assert group_by_pid(events) == {
        "groups": [
            {"pid": 10, "indices": [0, 1], "count": 2},
            {"pid": 11, "indices": [2], "count": 1},
        ],
        "count": 2,
    }


def test_get_events_is_the_only_full_record_result():
    event = _events()[1]
    result = get_events([event])

    assert result["count"] == 1
    assert result["events"][0]["message"] == event.message
    assert "events" not in filter_by_level([event], levels=["E"])
    assert len(tool_definitions()) == 8
