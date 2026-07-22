# macOS Performance Budgets

The release workflow measures the installed `/Applications/VibeCAD.app`. It runs
the probe after the exact PKG smoke test and before the test installation is
removed. The probe does not read or use provider credentials.

The report schema is `vibecad-macos-performance-report-v1`. The budget input
schema is `vibecad-macos-performance-budget-input-v1`. Both schemas use version
1. `tools/macos_performance_gate.py` rejects a missing field, an extra or missing
metric, a nonfinite value, a nonpositive value, a changed unit, an unknown schema,
an over-budget result, or a runtime waiver.

## Release budgets

| Metric | Maximum | Measurement |
| --- | ---: | --- |
| Cold launch | 30,000 ms | Time from process start to the probe script in the first process that uses a fresh, dedicated profile. |
| Warm launch | 15,000 ms | Time from process start to the probe script in the immediate second process that uses the same profile. |
| Medium document open | 5,000 ms | Open, recompute, and first GUI update for a native FCStd fixture with 64 `Part::Feature` objects. |
| Large document open | 15,000 ms | Open, recompute, and first GUI update for a native FCStd fixture with 256 `Part::Feature` objects. |
| First deterministic AI response | 30,000 ms | End-to-end `VibeCADSession.run_prompt()` time for a local deterministic text provider with no network access. |
| Viewport interaction | 2,000 ms | Slowest of 12 standard-view, fit, and GUI-update operations on the large fixture. |
| Revision apply | 10,000 ms | Promotion of one already validated native candidate through project state, canonical CAD, revision head, metadata, and audit persistence. |
| Peak memory | 4,096 MiB | Highest resident memory sampled for the installed workload process. |
| Worker cleanup | 3,000 ms | Start, wait, output close, and process cleanup through the bounded scripted-process runner. |
| Quit | 5,000 ms | Time from the workload quit marker until the installed process exits. |

The cold-launch value is a fresh-profile launch. The workflow does not purge the
macOS file cache. The report includes this definition and must not describe the
value as a physical-disk cold launch.

The AI value is deterministic local latency. It measures session setup, bounded
context creation, the local provider call, response persistence, and completion.
It is not OpenAI, Anthropic, gateway, network, or live-model latency. Live-provider
latency stays in provider-specific benchmark reports.

## Evidence metadata

Each report records:

- CPU architecture and logical CPU count.
- Operating-system version.
- Physical memory.
- Installed application path and version.
- CI state and runner name.
- Source commit.
- FreeCAD version and display mode.
- Provider mode and deterministic provider identity.
- Medium and large fixture object counts.
- The exact versioned budget input.

The release evidence directory stores `vibecad-macos-performance.json`. The
release gate verifies the report again before it accepts the result.

## Waiver rules

A command-line or report-only waiver is not permitted. The verifier requires an
empty `waivers` array.

A temporary budget change requires all of these items in one reviewed change:

1. A repeatable report from the same architecture and runner class.
2. The cause of the regression.
3. A named owner.
4. An expiry date that is not later than the next release milestone.
5. A tracked repair item.
6. An explicit change to the versioned budget input or to the default budget in
   `tools/macos_performance_gate.py`.

Do not edit a generated report to pass the gate. Do not use a hardware change to
hide a software regression. Record separate baselines when Apple Silicon and
Intel runners have materially different results.
