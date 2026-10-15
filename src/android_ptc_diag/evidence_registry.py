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
        self._handle_by_id: dict[str, str] = {}
        self._id_by_handle: dict[str, str] = {}

    def snapshot(self) -> list[EvidenceItem]:
        return [item.model_copy(deep=True) for item in self._items.values()]

    def model_items(self, items: Iterable[EvidenceItem]) -> list[dict[str, Any]]:
        """Serialize evidence with stable run-local handles for model consumption."""
        visible = []
        for item in items:
            handle = self._model_handle(item)
            payload = item.model_dump(mode="json", exclude={"evidence_id"})
            visible.append({"handle": handle, **payload})
        return visible

    def model_handles(self, items: Iterable[EvidenceItem]) -> list[str]:
        """Assign handles without serializing a second copy of the evidence."""
        return [self._model_handle(item) for item in items]

    def model_handle_paths(
        self, items: Iterable[EvidenceItem], *, tool_call_id: str
    ) -> list[dict[str, Any]]:
        """Point handles at observations already present in one tool result."""
        visible = []
        for item in items:
            provenance = next(
                (
                    source
                    for source in item.provenance
                    if source.origin == "tool_result"
                    and source.tool_call_id == tool_call_id
                ),
                None,
            )
            if provenance is None:
                raise ValueError(
                    f"evidence has no source path for tool call {tool_call_id}: "
                    f"{item.evidence_id}"
                )
            visible.append(
                {
                    "handle": self._model_handle(item),
                    "path": provenance.result_path,
                }
            )
        return visible

    def handle_map(self) -> dict[str, str]:
        return dict(self._id_by_handle)

    def _model_handle(self, item: EvidenceItem) -> str:
        if item.evidence_id not in self._items:
            raise ValueError(f"unregistered evidence id: {item.evidence_id}")
        handle = self._handle_by_id.get(item.evidence_id)
        if handle is None:
            handle = f"E{len(self._handle_by_id) + 1:03d}"
            self._handle_by_id[item.evidence_id] = handle
            self._id_by_handle[handle] = item.evidence_id
        return handle

    def resolve_references(
        self, diagnosis: RegistryBackedDiagnosis
    ) -> RegistryBackedDiagnosis:
        """Resolve model-facing handles without guessing or changing the registry."""
        payload = diagnosis.model_dump()
        for claim_index, claim in enumerate(payload["claims"]):
            for reference_index, handle in enumerate(claim["evidence_refs"]):
                evidence_id = self._id_by_handle.get(handle)
                if evidence_id is None:
                    raise EvidenceReferenceError(
                        f"unknown evidence handle: {handle}",
                        ["claims", claim_index, "evidence_refs", reference_index],
                    )
                claim["evidence_refs"][reference_index] = evidence_id
        return RegistryBackedDiagnosis.model_validate(payload)

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
            if subject_type not in {
                "process",
                "service",
                "mount",
                "cgroup",
            } or not isinstance(subject_id, str):
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
            entity_path = (
                ("entity_ref",)
                if isinstance(result.get("entity_ref"), dict)
                else (identity_field,)
            )
            item = self._add_entity(
                subject_type, subject_id, provenance(entity_path)
            )
            touched[item.evidence_id] = item
            linked_cgroup = result.get("cgroup_ref")
            if isinstance(linked_cgroup, dict):
                linked_type = linked_cgroup.get("entity_type")
                linked_id = linked_cgroup.get("entity_id")
                if linked_type == "cgroup" and isinstance(linked_id, str):
                    item = self._add_entity(
                        "cgroup", linked_id, provenance(("cgroup_ref",))
                    )
                    touched[item.evidence_id] = item
            for field, value in result.items():
                if field in {
                    identity_field,
                    "entity_ref",
                    "cgroup_ref",
                    "read_only",
                    "error",
                }:
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
