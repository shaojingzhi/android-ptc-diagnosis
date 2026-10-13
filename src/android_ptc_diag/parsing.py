"""Minimal parsers for the replay log formats used by M0."""

from __future__ import annotations

from datetime import datetime, tzinfo
import re
from zoneinfo import ZoneInfo

from .models import LogEvent


_LOGCAT_RE = re.compile(
    r"^\s*(?P<timestamp>\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    r"\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+(?P<level>[A-Z])\s+"
    r"(?P<tag>[^:]+):\s?(?P<message>.*)$"
)
_KMSG_RE = re.compile(r"^\s*\[\s*(?P<seconds>\d+(?:\.\d+)?)\s*\]\s*(?P<message>.*)$")
_LOGCAT_BUFFER_HEADER_RE = re.compile(
    r"^-+\s+beginning of (?:main|kernel|system|crash|radio|events)\s*$",
    re.IGNORECASE,
)


def _resolve_device_timezone(value: str | tzinfo | None) -> tzinfo | None:
    if value is None or isinstance(value, tzinfo):
        return value
    if isinstance(value, str):
        return ZoneInfo(value)
    raise TypeError("device_timezone must be an IANA name or tzinfo")


def _parse_logcat_timestamp(
    value: str, year: int, device_timezone: tzinfo | None
) -> datetime | None:
    for fmt in ("%m-%d %H:%M:%S.%f", "%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(f"{year} {value}", f"%Y {fmt}")
            return parsed.replace(tzinfo=device_timezone)
        except ValueError:
            continue
    return None


def parse_logcat(
    text: str,
    *,
    year: int = 1900,
    device_timezone: str | tzinfo | None = None,
) -> list[LogEvent]:
    """Parse Logcat while retaining source line provenance.

    Device-local timestamps remain naive by default for replay compatibility.
    Callers handling real captures can provide the device timezone explicitly.
    Logcat buffer headers are framing metadata and do not become events.
    """
    resolved_timezone = _resolve_device_timezone(device_timezone)
    events: list[LogEvent] = []
    for raw_line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if _LOGCAT_BUFFER_HEADER_RE.fullmatch(line):
            continue
        match = _LOGCAT_RE.match(raw_line)
        if not match:
            events.append(
                LogEvent(
                    source="logcat",
                    raw_line_number=raw_line_number,
                    message=line,
                )
            )
            continue
        groups = match.groupdict()
        events.append(
            LogEvent(
                source="logcat",
                raw_line_number=raw_line_number,
                timestamp=_parse_logcat_timestamp(
                    groups["timestamp"], year, resolved_timezone
                ),
                pid=int(groups["pid"]),
                tid=int(groups["tid"]),
                level=groups["level"],
                tag=groups["tag"].strip(),
                message=groups["message"],
            )
        )
    return events


def parse_kmsg(text: str) -> list[LogEvent]:
    """Parse kernel-like lines; monotonic seconds are not treated as wall time."""
    events: list[LogEvent] = []
    for raw_line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = _KMSG_RE.match(raw_line)
        message = match.group("message") if match else line
        monotonic_seconds = float(match.group("seconds")) if match else None
        events.append(
            LogEvent(
                source="kmsg",
                raw_line_number=raw_line_number,
                monotonic_seconds=monotonic_seconds,
                message=message,
            )
        )
    return events


def load_events(
    logcat_text: str,
    kmsg_text: str | None = None,
    *,
    year: int = 1900,
    device_timezone: str | tzinfo | None = None,
) -> list[LogEvent]:
    """Merge logcat and optional kmsg into one normalized, non-causal event list."""
    events = parse_logcat(logcat_text, year=year, device_timezone=device_timezone)
    if kmsg_text:
        events.extend(parse_kmsg(kmsg_text))
    return events
