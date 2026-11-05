# Android launch recovery

- Owner: luma-dev-2 / Codex Android integration chat
- Status: source and runtime 108 package checks passed; Dell update awaiting administrator authentication
- Branch: agent/1003/android-launch-recovery-20261104
- Worktree: /srv/dev/worktrees/luma-os/android-launch-recovery-20261104
- Base: fefe9c5cf6fc274cd38dc8f372e3a06659c0206e

A launch can race a watchdog recovery: Android loses its display, recovery
replaces the session, and the original host-window restoration sees an
incomplete package inventory. The desktop launch request was discarded.

The CLI now waits up to 30 seconds for an already-running recovery and replays
its launch once only after recovery or a session/container identity change.
Healthy launches keep one dispatch. Ordinary app errors and unchanged sessions
are not retried. The restore/close broker retains its caller checks, authority
and 45-second client deadline; this change never initiates a runtime recovery.
The runtime RPM and desktop pins advance together to 108. CI includes launch
recovery and existing-window deadline regressions.

## Validation

- `devctl run android-launch-recovery-final-tests --path /srv/dev/worktrees/luma-os/android-launch-recovery-20261104 --memory-gib 1 --disk-gib 0 --wait -- env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src/luma-android python3 -m unittest discover -s src/luma-android/tests -p 'test_*.py'`: 193 tests, 18 skipped, no failures (175 passed).
- Job: dev-android-launch-recovery-final-tests-3a2aaa56.service.
- Eleven new regressions cover bounded waits, failed recovery, a single replay,
  unchanged-session failures and alternate engines.
- Native runtime 108 RPM build: all 193 tests passed, none skipped; payload compilation, desktop files and permissions checks passed. Job: dev-android-runtime108-package-final-90d097f9.service.
- Package release contract: 121 pins matched, one existing mobile exception; existing release fragments passed.
- Two initial package attempts hit a nested image-decoder sandbox restriction. A disposable rootless build container allowed nested bwrap; host enforcement and shipped runtime sandbox policy were unchanged. No test was removed or skipped to build the RPM.
- RPM: build/packages/luma-android-runtime/RPMS/noarch/luma-android-runtime-0.1.0-1.luma.108.fc44.noarch.rpm; SHA256 925b146af82c13019d550282744a7b4ffbe749374d5bdf012928297df4402d81.
- A fixed-scope device update was staged through normal administrator authentication. Live application is not yet confirmed; Android apps were not restarted for this launcher-only update.
- No automated device GUI test or induced crash. A user-requested Angry Birds
  launch opened after the existing recovery completed. The initial SIGKILL of
  Android's shared compositor has no confirmed sender; this change does not
  claim to prevent that separate incident.

## Integration

Claude owns nightly release operations. This task does not build or publish
an OS nightly. Private device diagnostics remain outside Git.
