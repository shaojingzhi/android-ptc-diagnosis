# M1 Java crash fixture

This fixture is intentionally only one Activity and one manifest. Build it with a local Android app toolchain as package `com.example.m1crash`, then install the APK with `adb-agent app install`; launching the app deterministically throws `IllegalStateException`.

After installation, run from the repository root:

```bash
PYTHONPATH=src python scripts/m1_real_e2e.py --package com.example.m1crash
```

The E2E script uses `adb-agent app launch`, then reads unfiltered recent Logcat and checks for `FATAL EXCEPTION` / `AndroidRuntime` evidence.
