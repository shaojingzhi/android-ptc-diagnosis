import json
from pathlib import Path

import pytest

from android_ptc_diag.cases import load_runtime_state
from android_ptc_diag.models import LogEvent
from android_ptc_diag.primitives import (
    discover_runtime_entities,
    inspect_cgroup_state,
    inspect_cgroup_state_by_ref,
    inspect_mount_state_by_ref,
    inspect_process_state_by_ref,
    inspect_service_state_by_ref,
    typed_reference_tool_definitions,
    typed_reference_tool_handlers,
)


CASES = Path(__file__).parents[1] / "eval" / "cases"


def _runtime_state():
    return {
        "processes": {
            "runtime_service": {
                "pid": 2600,
                "state": "running",
                "cgroup_path": "/sys/fs/cgroup/workload/demo-42",
            },
        },
        "services": {
            "runtime_service": {"state": "starting", "ready": False},
            "vendor_bridge": {"state": "running", "ready": True},
        },
        "mounts": {
            "/container/runtime": {"available": True, "read_only_mount": True},
        },
        "cgroups": {
            "startup_scope_42": {
                "path": "/sys/fs/cgroup/workload/demo-42",
                "memory_max_bytes": 67108864,
                "event_counters_observation_status": "not_collected",
            }
        },
    }


def test_service_discovery_returns_canonical_names():
    result = discover_runtime_entities(_runtime_state(), entity_type="service")

    assert [entity["name"] for entity in result["entities"]] == [
        "runtime_service",
        "vendor_bridge",
    ]
    assert result["entities"][0]["attributes"]["state"] == "starting"
    assert result["entities"][0]["entity_ref"] == {
        "entity_type": "service",
        "entity_id": "service:runtime_service",
        "canonical_name": "runtime_service",
    }
    assert result["count"] == 2


def test_discovery_does_not_create_guessed_aliases():
    result = discover_runtime_entities(_runtime_state(), entity_type="service")

    assert "RuntimeService" not in {entity["name"] for entity in result["entities"]}


def test_process_discovery_preserves_name_and_pid():
    result = discover_runtime_entities(_runtime_state(), entity_type="process")

    entity = result["entities"][0]
    assert entity["name"] == "runtime_service"
    assert entity["entity_ref"]["entity_id"] == "process:runtime_service"
    assert entity["attributes"] == {
        "pid": 2600,
        "state": "running",
        "cgroup_path": "/sys/fs/cgroup/workload/demo-42",
    }


def test_mount_discovery_returns_canonical_paths():
    result = discover_runtime_entities(_runtime_state(), entity_type="mount")

    entity = result["entities"][0]
    assert entity["path"] == "/container/runtime"
    assert entity["entity_ref"]["entity_id"] == "mount:/container/runtime"
    assert entity["attributes"] == {"available": True, "read_only_mount": True}


def test_discovery_is_read_only_and_contains_no_diagnosis_fields():
    result = discover_runtime_entities(_runtime_state(), entity_type="service")
    serialized = json.dumps(result)

    assert result["read_only"] is True
    for forbidden in ("root_cause", "confidence", "failure_reason", "recommendation"):
        assert forbidden not in serialized


def test_cgroup_discovery_exposes_canonical_key_and_path_without_state():
    result = discover_runtime_entities(_runtime_state(), entity_type="cgroup")

    assert result["entities"] == [
        {
            "name": "startup_scope_42",
            "path": "/sys/fs/cgroup/workload/demo-42",
            "entity_ref": {
                "entity_type": "cgroup",
                "entity_id": "cgroup:startup_scope_42",
                "canonical_name": "startup_scope_42",
            },
        }
    ]
    assert "memory_max_bytes" not in json.dumps(result)
    assert "not_collected" not in json.dumps(result)


def test_discovery_rejects_unsupported_entity_types():
    with pytest.raises(ValueError, match="unsupported runtime entity type"):
        discover_runtime_entities(_runtime_state(), entity_type="device")


def test_discovered_references_drive_read_only_inspection():
    state = _runtime_state()
    service_ref = discover_runtime_entities(state, entity_type="service")["entities"][0][
        "entity_ref"
    ]
    process_ref = discover_runtime_entities(state, entity_type="process")["entities"][0][
        "entity_ref"
    ]
    mount_ref = discover_runtime_entities(state, entity_type="mount")["entities"][0][
        "entity_ref"
    ]
    cgroup_ref = discover_runtime_entities(state, entity_type="cgroup")["entities"][0][
        "entity_ref"
    ]

    assert inspect_service_state_by_ref(state, entity_ref=service_ref)["read_only"] is True
    assert inspect_process_state_by_ref(state, entity_ref=process_ref)["pid"] == 2600
    assert inspect_mount_state_by_ref(state, entity_ref=mount_ref)["available"] is True
    cgroup = inspect_cgroup_state_by_ref(state, entity_ref=cgroup_ref)
    assert cgroup["cgroup"] == "startup_scope_42"
    assert cgroup["path"] == "/sys/fs/cgroup/workload/demo-42"
    assert cgroup["memory_max_bytes"] == 67108864
    assert cgroup["entity_ref"] == cgroup_ref


def test_typed_inspection_rejects_unknown_and_wrong_type_references():
    state = _runtime_state()
    with pytest.raises(ValueError, match="unknown service entity reference"):
        inspect_service_state_by_ref(
            state,
            entity_ref={
                "entity_type": "service",
                "entity_id": "service:RuntimeService",
            },
        )
    with pytest.raises(ValueError, match="type mismatch"):
        inspect_service_state_by_ref(
            state,
            entity_ref={
                "entity_type": "process",
                "entity_id": "process:runtime_service",
            },
        )
    with pytest.raises(ValueError, match="canonical name mismatch"):
        inspect_service_state_by_ref(
            state,
            entity_ref={
                "entity_type": "service",
                "entity_id": "service:runtime_service",
                "canonical_name": "RuntimeService",
            },
        )
    with pytest.raises(ValueError, match="unknown cgroup entity reference"):
        inspect_cgroup_state_by_ref(
            state,
            entity_ref={
                "entity_type": "cgroup",
                "entity_id": "cgroup:missing",
            },
        )
    with pytest.raises(ValueError, match="type mismatch"):
        inspect_cgroup_state_by_ref(
            state,
            entity_ref={
                "entity_type": "process",
                "entity_id": "process:runtime_service",
            },
        )
    with pytest.raises(ValueError, match="canonical name mismatch"):
        inspect_cgroup_state_by_ref(
            state,
            entity_ref={
                "entity_type": "cgroup",
                "entity_id": "cgroup:startup_scope_42",
                "canonical_name": "other_scope",
            },
        )


def test_process_inspection_links_exact_cgroup_path_without_leaking_state():
    result = inspect_process_state_by_ref(
        _runtime_state(),
        entity_ref={
            "entity_type": "process",
            "entity_id": "process:runtime_service",
            "canonical_name": "runtime_service",
        },
    )

    assert result["cgroup_reference_status"] == "resolved"
    assert result["cgroup_ref"] == {
        "entity_type": "cgroup",
        "entity_id": "cgroup:startup_scope_42",
        "canonical_name": "startup_scope_42",
    }
    assert result["cgroup_path"] == "/sys/fs/cgroup/workload/demo-42"
    assert "memory_max_bytes" not in result
    assert "event_counters_observation_status" not in result


@pytest.mark.parametrize(
    ("process_record", "cgroups", "status"),
    [
        ({"pid": 1, "state": "running"}, {}, "not_collected"),
        (
            {"pid": 1, "state": "running", "cgroup_path": "/missing"},
            {"record": {"path": "/different"}},
            "not_found",
        ),
        (
            {"pid": 1, "state": "running", "cgroup_path": "/shared"},
            {"one": {"path": "/shared"}, "two": {"path": "/shared"}},
            "ambiguous",
        ),
    ],
)
def test_process_inspection_reports_unresolved_cgroup_without_failing(
    process_record, cgroups, status
):
    state = {"processes": {"worker": process_record}, "cgroups": cgroups}

    result = inspect_process_state_by_ref(
        state,
        entity_ref={
            "entity_type": "process",
            "entity_id": "process:worker",
            "canonical_name": "worker",
        },
    )

    assert result["cgroup_reference_status"] == status
    assert "cgroup_ref" not in result


def test_typed_tool_surface_replaces_free_cgroup_name_and_old_primitive_remains():
    definitions = typed_reference_tool_definitions()
    names = [item["function"]["name"] for item in definitions]
    discovery = next(
        item for item in definitions if item["function"]["name"] == "discover_runtime_entities"
    )
    handlers = typed_reference_tool_handlers([LogEvent(source="replay", message="x")], _runtime_state())

    assert "inspect_cgroup_state_by_ref" in names
    assert "inspect_cgroup_state" not in names
    assert "cgroup" in discovery["function"]["parameters"]["properties"]["entity_type"]["enum"]
    assert set(names) == set(handlers)
    assert inspect_cgroup_state(_runtime_state(), name="startup_scope_42")[
        "memory_max_bytes"
    ] == 67108864


def test_limited_case_cgroup_inspection_preserves_not_collected_fields():
    state = load_runtime_state(CASES / "startup_cgroup_oom_limited")
    reference = discover_runtime_entities(state, entity_type="cgroup")["entities"][0][
        "entity_ref"
    ]

    result = inspect_cgroup_state_by_ref(state, entity_ref=reference)

    assert result["event_counters_observation_status"] == "not_collected"
    assert result["current_and_peak_observation_status"] == "not_collected"
    assert result["host_memory_observation_status"] == "not_collected"
    assert "oom_kill" not in result


def test_entity_references_are_stable_and_preserve_canonical_identity():
    first = discover_runtime_entities(_runtime_state(), entity_type="service")
    second = discover_runtime_entities(_runtime_state(), entity_type="service")

    assert first == second
    assert [entity["entity_ref"]["entity_id"] for entity in first["entities"]] == [
        "service:runtime_service",
        "service:vendor_bridge",
    ]


def test_typed_inspection_has_no_diagnosis_leakage():
    state = _runtime_state()
    reference = discover_runtime_entities(state, entity_type="service")["entities"][0][
        "entity_ref"
    ]
    result = inspect_service_state_by_ref(state, entity_ref=reference)
    serialized = json.dumps(result)

    assert result["read_only"] is True
    for forbidden in ("root_cause", "confidence", "recommendation"):
        assert forbidden not in serialized
