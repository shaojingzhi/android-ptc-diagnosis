from __future__ import annotations

from collections import defaultdict

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

from .models import LogEvent


def cluster_events(events: list[LogEvent]) -> list[int]:
    """Annotate events with in-memory Drain3 clusters and return first indices.

    The event message is the only value sent to Drain3. Raw event fields and the
    original list order stay intact, so each representative remains traceable to
    its first source event.
    """
    # An explicit config keeps each run in memory and avoids loading a local
    # drain3.ini from the caller's working directory.
    miner = TemplateMiner(config=TemplateMinerConfig())
    indices_by_cluster: dict[str, list[int]] = defaultdict(list)
    template_by_cluster: dict[str, str] = {}

    for index, event in enumerate(events):
        result = miner.add_log_message(event.message)
        cluster_id = str(result["cluster_id"])
        event.template_id = cluster_id
        indices_by_cluster[cluster_id].append(index)
        template_by_cluster[cluster_id] = result["template_mined"]

    for cluster_id, indices in indices_by_cluster.items():
        repeat_count = len(indices)
        template = template_by_cluster[cluster_id]
        for index in indices:
            events[index].template = template
            events[index].repeat_count = repeat_count

    return [indices[0] for indices in indices_by_cluster.values()]
