from __future__ import annotations

from typing import Any, Iterable

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

from .models import LogEvent


def event_group_identity(
    event: LogEvent, index: int
) -> tuple[str, str | None, str | None, str]:
    """Return the deterministic compact identity for one normalized event.

    Unclustered events use their original index as a private template identity,
    so missing metadata never causes unrelated malformed lines to be merged.
    Missing tag and level values remain explicit ``None`` identity components.
    """
    template_identity = event.template_id or f"unclustered:{index}"
    return (event.source, event.tag, event.level, template_identity)


def group_events_by_identity(
    events: list[LogEvent], indices: Iterable[int] | None = None
) -> list[dict[str, Any]]:
    """Group indices by source, tag, level, and Drain3 template identity."""
    selected = range(len(events)) if indices is None else indices
    groups_by_identity: dict[
        tuple[str, str | None, str | None, str], dict[str, Any]
    ] = {}
    for index in selected:
        event = events[index]
        identity = event_group_identity(event, index)
        group = groups_by_identity.setdefault(
            identity,
            {
                "template_id": event.template_id,
                "template": event.template or event.message,
                "indices": [],
                "source": event.source,
                "level": event.level,
                "tag": event.tag,
            },
        )
        group["indices"].append(index)

    groups = list(groups_by_identity.values())
    for group in groups:
        group["indices"].sort()
        group["count"] = len(group["indices"])
        group["first_index"] = group["indices"][0]
        group["last_index"] = group["indices"][-1]
        group["representative_index"] = group["first_index"]
        pids = {events[index].pid for index in group["indices"]}
        group["pid"] = next(iter(pids)) if len(pids) == 1 else None

    groups.sort(key=lambda group: group["first_index"])
    for group_id, group in enumerate(groups):
        group["group_id"] = group_id
    return groups


def cluster_events(events: list[LogEvent]) -> list[int]:
    """Annotate events with in-memory Drain3 clusters and return first indices.

    The event message is the only value sent to Drain3. Raw event fields and the
    original list order stay intact, so each representative remains traceable to
    its first source event.
    """
    # An explicit config keeps each run in memory and avoids loading a local
    # drain3.ini from the caller's working directory.
    miner = TemplateMiner(config=TemplateMinerConfig())
    template_by_cluster: dict[str, str] = {}

    for index, event in enumerate(events):
        result = miner.add_log_message(event.message)
        cluster_id = str(result["cluster_id"])
        event.template_id = cluster_id
        template_by_cluster[cluster_id] = result["template_mined"]

    for event in events:
        event.template = template_by_cluster[event.template_id]

    groups = group_events_by_identity(events)
    for group in groups:
        for index in group["indices"]:
            events[index].repeat_count = group["count"]

    return [group["representative_index"] for group in groups]
