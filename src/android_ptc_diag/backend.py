from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .models import LogEvent, TriageRequest


class DeviceBackend(ABC):
    """Small boundary between diagnosis logic and device/replay infrastructure."""

    @abstractmethod
    def collect_logcat(self, request: TriageRequest) -> str:
        raise NotImplementedError

    def collect_kmsg(self, request: TriageRequest) -> str | None:
        return None

    def collect_events(self, request: TriageRequest) -> list[LogEvent]:
        from .parsing import load_events

        year = request.failure_timestamp.year if request.failure_timestamp else 1900
        return load_events(self.collect_logcat(request), self.collect_kmsg(request), year=year)

    def get_package_info(self, package: str) -> dict:
        return {}

    def get_process_state(self, package: str) -> dict:
        return {}


class ReplayBackend(DeviceBackend):
    def __init__(self, case_dir: str | Path):
        self.case_dir = Path(case_dir)

    def collect_logcat(self, request: TriageRequest) -> str:
        return (self.case_dir / "logcat.txt").read_text(encoding="utf-8")

    def collect_kmsg(self, request: TriageRequest) -> str | None:
        path = self.case_dir / "kmsg.txt"
        return path.read_text(encoding="utf-8") if path.exists() else None

    def get_package_info(self, package: str) -> dict:
        path = self.case_dir / "package.yaml"
        if not path.exists():
            return {}
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class AdbMcpBackend(DeviceBackend):
    """Integration boundary for adb-automation-mcp.

    M0 intentionally leaves transport details out. M1 will wire a minimal MCP
    client to the mature adb-automation-mcp server instead of reimplementing ADB.
    """

    def collect_logcat(self, request: TriageRequest) -> str:
        raise NotImplementedError("M1: connect adb-automation-mcp")
