# Preserve updater behavior while converging architecture/device builds

Owner: luma-dev-2, ARM desktop integration worktree.
Status: existing phone source behaviors preserved; combined new package build pending.

The private handoff revision76d780848 already selects updater release22. A common build must advance beyond22 and retain its existing behavior. Selected files from that exact revision preserve actionable Phosh notifications, activation of Depot at Updates over D-Bus, the native FP6 trial-boot health backend, and earlier boot/network update checks. This is a selective updater-component convergence; it does not import device kernels or the private repository wholesale. Existing public initial-channel policy remains in the shared engine.

Validation: bash/sh syntax and notifier Python AST parsing passed. Normally admitted job bb9ec951 ran17 greenboot tests:16 passed, one real-device read-only FP6 query skipped because no physical device is attached to this test. The earlier MAIN-disk admission refused before test execution; the successful job used the existing task HDD. No physical phone boot-health result or live Phosh notification test is claimed. Run the combined updater suite and native OSTree checks in the final package producer after integration.
