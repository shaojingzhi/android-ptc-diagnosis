"""Run-local, deterministic EvidenceItem construction from model-visible data."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

from .models import (
    EvidenceItem,
    EvidenceObservation,
    EvidenceProvenance,
    EvidenceSubject,
    RegistryBackedDiagnosis,
)


class EvidenceReferenceError(ValueError):
    def __init__(self, message: str, path: list[str | int]):
        super().__init__(message)
        self.path = path


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _field_token(field: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", field).strip("_")
    return value or "value"


def _walk_dicts(value: Any, path: tuple[str | int, ...] = ()) -> Iterable[tuple[tuple[str | int, ...], dict[str, Any]]]:
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from _walk_dicts(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_dicts(item, (*path, index))


class EvidenceRegistry:
    """Evidence supplied during one run; instances never share mutable state."""

    def __init__(self) -> None:
        self._items: dict[str, EvidenceItem] = {}

    def snapshot(self) -> list[EvidenceItem]:
        return [item.model_copy(deep=True) for item in self._items.values()]

    def add_compact_view(self, compact: dict[str, Any]) -> list[EvidenceItem]:
        added = []
        for position, group in enumerate(compact.get("groups", [])):
            representative = group.get("representative_index")
            group_id = group.get("group_id")
            if not isinstance(representative, int) or not isinstance(group_id, int):
                continue
            provenance = EvidenceProvenance(
                origin="compact_group",
                event_index=representative,
                group_id=group_id,
                result_path=["compact_evidence", "groups", position],
            )
            added.append(
                self._add_observation(
                    "log_event",
                    "event",
                    f"event:{representative}",
                    "compact_group",
                    group,
                    provenance,
                )
            )
        return added

    def add_tool_result(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        result: dict[str, Any],
    ) -> list[EvidenceItem]:
        if result.get("error") is not None:
            return []
        touched: dict[str, EvidenceItem] = {}

        def provenance(path: tuple[str | int, ...], **identity: int) -> EvidenceProvenance:
            return EvidenceProvenance(
                origin="tool_result",
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result_path=list(path),
                **identity,
            )

        for path, value in _walk_dicts(result):
            event_index = value.get("index")
            if isinstance(event_index, int) and "message" in value:
                item = self._add_observation(
                    "log_event",
                    "event",
                    f"event:{event_index}",
                    "normalized_event",
                    value,
                    provenance(path, event_index=event_index),
                )
                touched[item.evidence_id] = item

            group_id = value.get("group_id")
            representative = value.get("representative_index", value.get("first_index"))
            if isinstance(group_id, int) and isinstance(representative, int):
                item = self._add_observation(
                    "log_event",
                    "event",
                    f"event:{representative}",
                    "compact_group",
                    value,
                    provenance(path, event_index=representative, group_id=group_id),
                )
                touched[item.evidence_id] = item

        indices = result.get("indices")
        if isinstance(indices, list):
            for position, event_index in enumerate(indices):
                if not isinstance(event_index, int):
                    continue
                item = self._add_observation(
                    "log_event",
                    "event",
                    f"event:{event_index}",
                    f"{tool_name}.matched_index",
                    event_index,
                    provenance(("indices", position), event_index=event_index),
                )
                touched[item.evidence_id] = item

        self._add_discovered_entities(result, provenance, touched)
        self._add_runtime_record(result, provenance, touched)
        return list(touched.values())

    def validate_references(self, diagnosis: RegistryBackedDiagnosis) -> None:
        for claim_index, claim in enumerate(diagnosis.claims):
            for reference_index, evidence_id in enumerate(claim.evidence_refs):
                if evidence_id not in self._items:
                    raise EvidenceReferenceError(
                        f"unknown evidence id: {evidence_id}",
                        ["claims", claim_index, "evidence_refs", reference_index],
                    )

    def _add_discovered_entities(self, result, provenance, touched) -> None:
        entities = result.get("entities")
        if not isinstance(entities, list):
            return
        for position, entity in enumerate(entities):
            if not isinstance(entity, dict) or not isinstance(entity.get("entity_ref"), dict):
                continue
            reference = entity["entity_ref"]
            subject_type = reference.get("entity_type")
            subject_id = reference.get("entity_id")
            if subject_type not in {"process", "service", "mount"} or not isinstance(subject_id, str):
                continue
            base_path = ("entities", position)
            item = self._add_entity(
                subject_type,
                subject_id,
                provenance((*base_path, "entity_ref")),
            )
            touched[item.evidence_id] = item
            attributes = entity.get("attributes")
            if isinstance(attributes, dict):
                for field, value in attributes.items():
                    item = self._add_observation(
                        "runtime_state",
                        subject_type,
                        subject_id,
                        field,
                        value,
                        provenance((*base_path, "attributes", field)),
                    )
                    touched[item.evidence_id] = item

    def _add_runtime_record(self, result, provenance, touched) -> None:
        identities = {
            "process": "process",
            "service": "service",
            "mount": "mount",
            "cgroup": "cgroup",
        }
        for identity_field, subject_type in identities.items():
            identifier = result.get(identity_field)
            if not isinstance(identifier, str):
                continue
            subject_id = f"{subject_type}:{identifier}"
            if subject_type != "cgroup":
                item = self._add_entity(
                    subject_type, subject_id, provenance((identity_field,))
                )
                touched[item.evidence_id] = item
            for field, value in result.items():
                if field in {identity_field, "read_only", "error"}:
                    continue
                item = self._add_observation(
                    "runtime_state",
                    subject_type,
                    subject_id,
                    field,
                    value,
                    provenance((field,)),
                )
                touched[item.evidence_id] = item
            return

    def _add_entity(
        self,
        subject_type: str,
        subject_id: str,
        provenance: EvidenceProvenance,
    ) -> EvidenceItem:
        return self._add(
            EvidenceItem(
                evidence_id=f"entity:{subject_id}",
                source_type="entity",
                subject=EvidenceSubject(type=subject_type, id=subject_id),
                provenance=[provenance],
            )
        )

    def _add_observation(
        self,
        source_type: str,
        subject_type: str,
        subject_id: str,
        field: str,
        value: Any,
        provenance: EvidenceProvenance,
    ) -> EvidenceItem:
        identity = {
            "source_type": source_type,
            "subject": subject_id,
            "field": field,
            "value": value,
        }
        evidence_id = (
            f"{source_type}:{subject_id}:{_field_token(field)}:{_digest(identity)}"
        )
        return self._add(
            EvidenceItem(
                evidence_id=evidence_id,
                source_type=source_type,
                subject=EvidenceSubject(type=subject_type, id=subject_id),
                observation=EvidenceObservation(field=field, value=value),
                provenance=[provenance],
            )
        )

    def _add(self, item: EvidenceItem) -> EvidenceItem:
        existing = self._items.get(item.evidence_id)
        if existing is None:
            self._items[item.evidence_id] = item
            return item
        comparable_existing = existing.model_dump(exclude={"provenance"})
        comparable_new = item.model_dump(exclude={"provenance"})
        if comparable_existing != comparable_new:
            raise ValueError(f"conflicting evidence id: {item.evidence_id}")
        for provenance in item.provenance:
            if provenance not in existing.provenance:
                existing.provenance.append(provenance)
        return existing
