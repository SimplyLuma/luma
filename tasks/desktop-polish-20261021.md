# Desktop updates — 2026-10-21

Status: active. Owner: luma-dev-2. Source task: desktop-polish-20261021.

Nightly 20261020.1 is already published. These changes are a separate update batch.

## Manual partition planning

Forward the selected device map from the manual screen through applyPlan into
Anaconda storage planning. Without it, Continue raised a JavaScript TypeError
when resolving the EFI partition's parent disk.

Validation: the real manual screen's Continue handler reproduced the exact
failure before the change. Four cases pass after the change: same/separate EFI
disk and preserving/changing firmware startup order. The native D-Bus transport
is substituted in these tests; the screen, planning wrapper and disk-selection
logic are product code. The normal Atlas20 package producer passed all39 JavaScript
tests, Python/viewer checks and product bundle/RPM checks. This does not establish
an additional installed-system or ISO qualification. No new image is published.

## Remaining batch

Login surfaces, battery percentage, Night Light scheduling, one-percent media
keys, dock shortcuts, Filer context menus and shared search-control lifetime
changes are being qualified. Filer's installed candidate passed Applications
population within two seconds, Name-only list columns and repeated focused-search
navigation. Its shared runtime still has an unresolved release-cohort gate.
The five-monitor report remains unreproduced. Codec delivery is research only.
Drawer appearance confirmation and final release qualification remain pending.
