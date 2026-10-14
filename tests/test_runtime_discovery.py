import json

import pytest

from android_ptc_diag.primitives import discover_runtime_entities


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
    assert result["count"] == 2


def test_discovery_does_not_create_guessed_aliases():
    result = discover_runtime_entities(_runtime_state(), entity_type="service")

    assert "RuntimeService" not in {entity["name"] for entity in result["entities"]}


def test_process_discovery_preserves_name_and_pid():
    result = discover_runtime_entities(_runtime_state(), entity_type="process")

    assert result["entities"] == [
        {
            "name": "runtime_service",
            "attributes": {"pid": 2600, "state": "running"},
        }
    ]


def test_mount_discovery_returns_canonical_paths():
    result = discover_runtime_entities(_runtime_state(), entity_type="mount")

    assert result["entities"] == [
        {
            "path": "/container/runtime",
            "attributes": {"available": True, "read_only_mount": True},
        }
    ]


def test_discovery_is_read_only_and_contains_no_diagnosis_fields():
    result = discover_runtime_entities(_runtime_state(), entity_type="service")
    serialized = json.dumps(result)

    assert result["read_only"] is True
    for forbidden in ("root_cause", "confidence", "failure_reason", "recommendation"):
        assert forbidden not in serialized


def test_discovery_rejects_unsupported_entity_types():
    with pytest.raises(ValueError, match="unsupported runtime entity type"):
        discover_runtime_entities(_runtime_state(), entity_type="cgroup")
