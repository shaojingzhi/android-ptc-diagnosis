# Public Android Logcat validation

This validation answers a narrow authenticity question: can the existing Android parser, Drain3
grouping, compact representation, and indexed evidence access process public records that were
actually captured from Android devices? It does not turn an unlabeled public corpus into a root-cause
benchmark and does not expand the product scope to generic Android application diagnosis.

## Sources and boundaries

The fixtures live under `eval/public_logcat/`, outside the frozen replay benchmark.

| Fixture | Public source | Preserved material | Evaluation boundary |
| --- | --- | --- | --- |
| `loghub_android_v1_2k` | [Loghub Android](https://github.com/logpai/loghub/tree/dd61d0952749ee7963bde24220d1be5ede023033/Android) | The complete 2,000-record `Android_2k.log`, byte-for-byte | Real phone Logcat for ingestion/noise/compaction tests; no incident or root-cause labels |
| `qfield_native_crash_excerpt` | [QField crash Gist](https://gist.github.com/jhonalex06/083b7edd3434619e659441ef73d639fa) | Source lines 5126–5170, without content edits | A bounded public native-crash sequence; not a complete capture or independently verified fix |

`source.yaml` pins the upstream revision or raw URL, whole-source metadata where available, local
artifact hash, transformation boundary, provenance claim, and claims that remain unsupported. The
Loghub fixture follows the dataset's research/academic-use notice. The Gist has no explicit license,
so this repository keeps only a short attributed operational excerpt and does not assume broader
redistribution rights.

ABRLog is another strong public source: it contains 45 Android bugreport captures from 15 devices,
including 4.59 million extracted Logcat records, and documents deterministic pseudonymization. It was
not vendored here because the extracted archive is large and the corpus has no hand-annotated ground
truth. It remains a candidate for externally cached, opt-in scale testing rather than a normal CI
fixture.

## Deterministic results

| Fixture | Raw bytes | Events parsed | Fallback rows | Tags | Levels | Compact groups |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| Loghub Android_v1 | 279,076 | 2,000 | 0 | 19 | D 650, E 3, I 920, V 257, W 170 | 164 |
| QField crash excerpt | 6,075 | 45 | 0 | 13 | D 1, E 6, F 18, I 15, W 5 | 38 |

Every record parses as standard Android threadtime Logcat with timestamp, PID, TID, level, and tag.
Every normalized event belongs to exactly one `source + tag + level + template_id` group, and the
original source-line position remains in `raw_line_number`.

The QField excerpt preserves four independently retrievable points in the observed sequence:

- event 5: native `SIGSEGV` for the QField process;
- event 17: tombstone reports a null-pointer dereference;
- event 24: the first `FeatureModel::applyGeometry()` backtrace frame;
- event 43: ActivityManager reports that the process died.

It also contains unrelated or secondary error-looking records around the crash, including a
performance-profile error, SELinux denials from `crash_dump32`, lowmemorykiller write failure, and
SurfaceFlinger cleanup errors. Their presence is useful evidence that a real Logcat cannot be reduced
to “all E/F records are causal.”

The full Loghub sample reduces from 279,076 raw bytes to 164 compact groups, but its three E-level
records are not labeled incident causes. That result validates parsing and duplicate compression,
not diagnosis accuracy or semantic noise classification.

## What this adds—and what it does not

The repository now has three distinct real-data checks:

1. locally reproduced Emulator crash capture, with a known trigger;
2. public real Android Logcat for parser/compaction robustness;
3. public Linux-container issue excerpts for cross-domain ingestion boundaries.

Only the first has a locally controlled failure trigger. Public Logcat provenance does not by itself
provide a trustworthy primary-failure label. No model API was called, no diagnosis score was produced,
and the frozen synthetic benchmark remains unchanged.
