"""Generate the deterministic binder_flood_001 synthetic stress fixture.

This case is inspired by common Android service-death and Binder-failure
patterns. It contains no proprietary Huawei logs or production data.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_EVENT_COUNT = 500
PRE_ROOT_NOISE_COUNT = 20
SERVICE_UNAVAILABLE_COUNT = 80
POST_ROOT_NOISE_COUNT = 48
DEFAULT_BINDER_COUNT = 350


def _timestamp(start: datetime, offset_ms: int) -> str:
    return (start + timedelta(milliseconds=offset_ms)).strftime("%m-%d %H:%M:%S.%f")[:-3]


def _line(timestamp: str, pid: int, tid: int, level: str, tag: str, message: str) -> str:
    return f"{timestamp} {pid:5d} {tid:5d} {level} {tag}: {message}"


def generate_binder_flood(event_count: int = DEFAULT_EVENT_COUNT) -> list[str]:
    fixed_count = PRE_ROOT_NOISE_COUNT + 1 + SERVICE_UNAVAILABLE_COUNT + POST_ROOT_NOISE_COUNT + 1
    binder_count = event_count - fixed_count
    if binder_count < 1:
        raise ValueError(f"event_count must be at least {fixed_count + 1}")

    pre_noise = [
        (700, 701, "W", "ThermalHAL", "thermal sensor read timeout"),
        (800, 801, "I", "WifiService", "background scan cache refresh complete"),
        (810, 811, "I", "AudioService", "idle audio route unchanged"),
        (820, 821, "I", "PackageManager", "background package scan completed"),
        (830, 831, "I", "ActivityTaskManager", "historical task cleanup deferred"),
    ]
    post_noise = [
        (840, 841, "I", "ActivityManager", "unrelated background process heartbeat"),
        (850, 851, "W", "WifiService", "background scan result unavailable"),
        (860, 861, "I", "AudioService", "idle audio route unchanged"),
        (870, 871, "I", "PackageManager", "package scan completed"),
        (880, 881, "I", "ActivityTaskManager", "unrelated activity lifecycle update"),
    ]

    lines: list[str] = []
    pre_start = datetime(2026, 10, 3, 11, 19, 40)
    for index in range(PRE_ROOT_NOISE_COUNT):
        pid, tid, level, tag, message = pre_noise[index % len(pre_noise)]
        lines.append(_line(_timestamp(pre_start, index * 100), pid, tid, level, tag, message))

    root_time = datetime(2026, 10, 3, 11, 20, 2, 100000)
    lines.append(
        _line(
            _timestamp(root_time, 0),
            2200,
            2200,
            "E",
            "ContainerRuntime",
            "bridge_service terminated unexpectedly",
        )
    )
    for index in range(binder_count):
        lines.append(
            _line(
                _timestamp(root_time, 100 + index * 10),
                2300,
                2310,
                "E",
                "Binder",
                "binder transaction failed for bridge_service",
            )
        )

    service_start = 100 + binder_count * 10
    for index in range(SERVICE_UNAVAILABLE_COUNT):
        lines.append(
            _line(
                _timestamp(root_time, service_start + index * 20),
                2450,
                2450,
                "E",
                "ClientRuntime",
                "required service unavailable: bridge_service",
            )
        )

    noise_start = service_start + SERVICE_UNAVAILABLE_COUNT * 20
    for index in range(POST_ROOT_NOISE_COUNT):
        pid, tid, level, tag, message = post_noise[index % len(post_noise)]
        lines.append(
            _line(_timestamp(root_time, noise_start + index * 40), pid, tid, level, tag, message)
        )

    lines.append(
        _line(
            "10-03 11:20:09.900",
            2450,
            2450,
            "E",
            "AndroidRuntime",
            "FATAL EXCEPTION: main; startup failed because bridge_service is unavailable",
        )
    )
    assert len(lines) == event_count
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate binder_flood_001 logcat fixture")
    parser.add_argument("--event-count", type=int, default=DEFAULT_EVENT_COUNT)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parents[1] / "eval" / "cases" / "binder_flood_001" / "logcat.txt",
    )
    args = parser.parse_args()
    lines = generate_binder_flood(args.event_count)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(lines)} events to {args.output}")


if __name__ == "__main__":
    main()
