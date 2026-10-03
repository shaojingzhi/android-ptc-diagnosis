from __future__ import annotations

from enum import StrEnum


class ExecutionMode(StrEnum):
    # RAW and DIRECT are intentionally separate baselines.
    RAW = "raw"
    FIXED = "fixed"
    DIRECT = "direct"
    PTC = "ptc"
    HYBRID = "hybrid"


class PtcExecutor:
    """Thin adapter around an existing PTC runtime (target: open-ptc-agent)."""

    def run(self, *args, **kwargs):
        raise NotImplementedError("M3: integrate mature PTC backend")


class HybridExecutor:
    """M4: PTC triage + at most one direct evidence-acquisition round."""

    max_investigation_rounds = 1

    def run(self, *args, **kwargs):
        raise NotImplementedError("M4: implement after PTC badcases exist")
