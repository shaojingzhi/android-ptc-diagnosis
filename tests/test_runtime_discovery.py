import json

import pytest

from android_ptc_diag.primitives import (
    discover_runtime_entities,
    inspect_mount_state_by_ref,
    inspect_process_state_by_ref,
    inspect_service_state_by_ref,
)


def _runtime_state():
    return {
        "processes": {
            "runtime_service": {"pid": 2600, "state": "running"},
        },
        "services": {
            "runtime_service": {"state": "starting", "ready": False},
            "vendor_bridge": {"state": "running", "ready": True},
        },
        "mounts": {
            "/container/runtime": {"available": True, "read_only_mount": True},
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
    assert entity["attributes"] == {"pid": 2600, "state": "running"}


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


def test_discovery_rejects_unsupported_entity_types():
    with pytest.raises(ValueError, match="unsupported runtime entity type"):
        discover_runtime_entities(_runtime_state(), entity_type="cgroup")


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

    assert inspect_service_state_by_ref(state, entity_ref=service_ref)["read_only"] is True
    assert inspect_process_state_by_ref(state, entity_ref=process_ref)["pid"] == 2600
    assert inspect_mount_state_by_ref(state, entity_ref=mount_ref)["available"] is True


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
