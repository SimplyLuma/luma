# Surface Pro 12-inch kernel changes

The shared ARM desktop remains unchanged. This device's source lock is
`config/kernel/surface-pro12-7.3.json`: upstream Linux 7.3 plus selected hardware
patches from miasvanklei/Gentoo-overlay at
`c570db86262ffac9b636acf45b1557c06e0d7ebd`. Original kernel changes retain the
licenses and notices of the upstream files (principally GPL-2.0-only; device
trees may use dual GPL/BSD licensing). Preserve every patch author header.

The lock lists archive hashes and every applied patch hash. Fourteen patches
under `7.3/` are context rebases of that exact overlay revision, retaining its
changes. The other patches are fetched from the pinned overlay rather than
copied into this repository. The ordered cohort was replayed against pristine
7.3 without fuzz or relaxed checks; all 72 affected files matched the initial
build candidate. This is source evidence, not hardware qualification.

Excluded changes include the optional EL2 boot path, bypassing TrustZone for
video firmware, patches for other Surface models, suppression of ath11k warnings,
and removal of compiler diagnostics. The PMIC GLINK probe-order workaround is
still provisional upstream work; keep it scoped here and replace it when a
proper upstream resolution is available. A pinned input is not automatically
an upstream-approved fix.

`prepare-source.py` prepares an offline-replayable source tree from the lock.
`configure-surface-pro12.sh` verifies the exact Fedora base configuration and
merges the device fragment. SELinux, module signing and BPF/BTF remain enabled.
Build keys, firmware, object files, kernels and ISO artifacts never enter Git.
