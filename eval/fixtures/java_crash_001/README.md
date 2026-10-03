# M1 Java crash fixture

This is a minimal, single-module Android application. It has one Activity, no UI, no Compose, and no application dependencies. Starting the Activity deterministically throws `IllegalStateException("M1 deliberate Java crash")`.

## Build and install

Requirements: JDK 17, Android SDK platform 35, and Gradle 9.6 or a compatible Android Studio/Gradle toolchain. AGP 9.4.0 is declared in the root `build.gradle`.

From this directory:

```bash
gradle :app:assembleDebug
adb-agent app install app/build/outputs/apk/debug/app-debug.apk --reinstall
```

The APK application ID is `com.example.m1crash`.

## Run the M1 E2E

From the repository root, after a device or emulator is reachable:

```bash
PYTHONPATH=src python3 scripts/m1_real_e2e.py --package com.example.m1crash --lines 1000
```

The E2E script uses `adb-agent app launch`, then reads unfiltered recent Logcat and checks for `FATAL EXCEPTION` / `AndroidRuntime` evidence.
