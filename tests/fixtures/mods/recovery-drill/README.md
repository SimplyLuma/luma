# Luma Mods recovery drill payload

This fixture is intentionally unsafe to ship. Its only file replaces the
candidate deployment's Mods promotion command with `/usr/bin/false`. The
observer can journal the candidate and the desktop can still start, but the
candidate cannot become known-good; the fixed watchdog must therefore invoke
the rpm-ostree rollback path.

Build it only with `scripts/packages/build-luma-mod-recovery-drill.sh`. The
script requires `LUMA_MOD_RECOVERY_DRILL=1`, produces an isolated test RPM, and
never adds that RPM to either desktop or mobile package inputs.
