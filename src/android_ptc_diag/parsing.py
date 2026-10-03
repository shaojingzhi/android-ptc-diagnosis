"""Minimal parsers for the replay log formats used by M0."""

from __future__ import annotations

from datetime import datetime
import re

from .models import LogEvent


_LOGCAT_RE = re.compile(
    r"^\s*(?P<timestamp>\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    r"\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+(?P<level>[A-Z])\s+"
    r"(?P<tag>[^:]+):\s?(?P<message>.*)$"
)
_KMSG_RE = re.compile(r"^\s*\[\s*(?P<seconds>\d+(?:\.\d+)?)\s*\]\s*(?P<message>.*)$")


def _parse_logcat_timestamp(value: str, year: int) -> datetime | None:
    for fmt in ("%m-%d %H:%M:%S.%f", "%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(f"{year} {value}", f"%Y {fmt}")
        except ValueError:
            continue
    return None


def parse_logcat(text: str, *, year: int = 1900) -> list[LogEvent]:
    """Parse Android-style logcat, retaining malformed lines as message-only events."""
    events: list[LogEvent] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _LOGCAT_RE.match(raw_line)
        if not match:
            events.append(LogEvent(source="logcat", message=line))
            continue
        groups = match.groupdict()
        events.append(
            LogEvent(
                source="logcat",
                timestamp=_parse_logcat_timestamp(groups["timestamp"], year),
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
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _KMSG_RE.match(raw_line)
        message = match.group("message") if match else line
        events.append(LogEvent(source="kmsg", message=message))
    return events


def load_events(logcat_text: str, kmsg_text: str | None = None, *, year: int = 1900) -> list[LogEvent]:
    """Merge logcat and optional kmsg into one normalized, non-causal event list."""
    events = parse_logcat(logcat_text, year=year)
    if kmsg_text:
        events.extend(parse_kmsg(kmsg_text))
    return events
