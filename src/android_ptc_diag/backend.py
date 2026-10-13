from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
import subprocess

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

        reference_time = request.incident_time or request.failure_timestamp
        year = reference_time.year if reference_time else 1900
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


class AdbCliError(RuntimeError):
    """A failed or unavailable upstream adb-agent command."""


class AdbCliBackend(DeviceBackend):
    """Thin adapter for the upstream Android-Debug-Bridge-MCP CLI."""

    def __init__(
        self,
        executable: str = "adb-agent",
        device: str | None = None,
        log_lines: int = 200,
    ):
        self.executable = executable
        self.device = device
        self.log_lines = log_lines

    def _run(self, args: list[str]) -> str:
        command = [self.executable]
        if self.device:
            command.extend(["--device", self.device])
        command.extend(args)
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise AdbCliError(
                f"Cannot find {self.executable!r}; install android-debug-bridge-mcp "
                "and ensure adb-agent is on PATH"
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip() or "no error details"
            raise AdbCliError(f"adb-agent command failed ({result.returncode}): {detail}")
        return result.stdout

    def doctor(self) -> str:
        return self._run(["doctor"])

    def clear_logcat(self) -> str:
        return self._run(["system", "clear-logcat"])

    def launch_app(self, package: str) -> str:
        return self._run(["app", "launch", package])

    def collect_logcat(self, request: TriageRequest) -> str:
        # Intentionally collect unfiltered logcat: a crashed app may no longer
        # have a PID for adb-agent's package filter to use.
        return self._run(["system", "logcat", "--lines", str(self.log_lines)])
