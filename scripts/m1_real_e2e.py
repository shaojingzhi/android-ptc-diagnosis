"""Run the manual M1 real-device crash flow through adb-agent."""

from __future__ import annotations

import argparse
from datetime import datetime
import sys
import time

from android_ptc_diag.backend import AdbCliBackend, AdbCliError
from android_ptc_diag.models import TriageRequest
from android_ptc_diag.parsing import parse_logcat


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, help="Installed deliberate-crash app package")
    parser.add_argument("--device", help="adb-agent device serial, prefix, or model")
    parser.add_argument("--adb-agent", default="adb-agent", help="adb-agent executable")
    parser.add_argument("--lines", type=int, default=200, help="Unfiltered logcat lines to read")
    parser.add_argument("--settle-seconds", type=float, default=1.0)
    args = parser.parse_args()

    backend = AdbCliBackend(
        executable=args.adb_agent,
        device=args.device,
        log_lines=args.lines,
    )
    try:
        doctor_output = backend.doctor()
        doctor_failures = [
            line.strip() for line in doctor_output.splitlines() if line.strip().startswith("fail ")
        ]
        if doctor_failures:
            raise AdbCliError("adb-agent doctor reported: " + "; ".join(doctor_failures))
        print("device reachable: yes")
        backend.clear_logcat()
        backend.launch_app(args.package)
        print("crash triggered: launch command returned")
        time.sleep(args.settle_seconds)
        raw_logcat = backend.collect_logcat(
            TriageRequest(symptom="deliberate Java crash", target_package=args.package)
        )
    except AdbCliError as exc:
        print(f"M1 E2E blocked: {exc}", file=sys.stderr)
        return 2

    events = parse_logcat(raw_logcat, year=datetime.now().year)
    crash_events = [
        event
        for event in events
        if "FATAL EXCEPTION" in event.message or event.tag == "AndroidRuntime"
    ]
    print(f"raw Logcat captured: {len(raw_logcat.splitlines())} lines")
    print(f"normalized LogEvent[]: {len(events)} events")
    print(f"crash evidence found: {len(crash_events)} events")
    if not crash_events:
        print("M1 E2E failed: no FATAL EXCEPTION or AndroidRuntime evidence", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
