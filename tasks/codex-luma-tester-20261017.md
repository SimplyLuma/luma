# Luma Tester — Linux first

- Owner: Codex, luma-dev-2, current tester chat.
- Status: launcher milestone ready for review; complete Luma guest integration pending.
- Branch: codex/luma-tester, based on origin/main 925db44.
- Worktree: /srv/dev/worktrees/luma-os/codex-luma-tester-20261017.
- Scope: LumaUI Linux launcher, mobile-first and desktop profiles, x86_64 and
  aarch64 guest definitions, verified image ingestion, private persistent sessions,
  localhost SSH, source sync/app restart workflow, image/agent documentation.
- Owned: src/luma-tester, docs/developer-platform/tester*.md, README/BUILDING
  navigation, this task record. No app-porting source lanes modified.
- No publication, OS deployment, signing, physical device operations, or user data
  changes. Host platform packages and integration checkout remain unchanged.

## Current handoff

The user explicitly rejected a generic GTK final interface. The revised GUI
requires real LumaUI: AppWindow/frame, Island, Card, type roles, icons and shared
palette. Flow: Try Luma / Develop for Luma → Mobile / Desktop → Open Luma.
Mobile is first/default. Architecture is chosen automatically. Development
controls appear only on Develop. Image import and workspace diagnostics live in
Advanced. Imports persist locally; packaging may supply LUMA_TESTER_CATALOG.

Backend accepts trusted local manifests referencing HTTPS or local standalone
qcow2 files, checks SHA-256/size, maintains private overlays/keys/seeds and locks,
chooses KVM or TCG, exposes localhost-only SSH, QMP controls/screenshots, source
sync and a foreground app restart watcher. CLI remains independent of GTK/LumaUI.
The wheel is a launcher package, not a self-contained QEMU/LumaUI/guest bundle.

There is no qualified public simulator image/catalog in this source milestone.
Existing FP6 twin starts from Fedora Cloud and experimental Phosh; it is not
proof of current Luma Mobile parity. Full bootable Luma Mobile/desktop guests,
real guest source-sync/restart qualification, image distribution, and portable
host/runtime packaging remain. Next useful action: produce source-identified
QEMU guest images using the documented contract, first mobile; boot/qualify them
with actual Luma shell/apps before advertising a click-to-try download. ARM
emulator is absent on this host: ARM argv selection is checked, ARM execution is
unqualified. macOS/Windows hosts remain deferred per the Linux-first request.

Git fetch encountered a pre-existing missing object under a Codex turn-diff
capture ref in the shared repository. Existing origin/main resolves to 925db44;
used that source without altering shared refs or unrelated worktrees.

## Validation and artifacts

- `devctl run luma-tester-source-final ... --memory-gib 1 --wait -- env
  PYTHONPATH=src/luma-tester python3 -m unittest discover -s
  src/luma-tester/tests -v`: 30 tests passed. Unit
  dev-luma-tester-source-final-6f51d1c9.service, artifacts
  /srv/dev/accounts/second/artifacts/dev-luma-tester-source-final-6f51d1c9.
  Includes real x86 QEMU/TCG startup, CLI-held disk lease, termination forwarding,
  QMP pause/resume and PPM capture; fixture disks intentionally have no OS.
- Checked-in `src/luma-tester/tests/ui_smoke.py` under Xvfb with real LumaUI:
  passed Try/Develop navigation, mobile default, target toggle, developer
  disclosure, Advanced, keyboard focus, narrow/dark rendering. Unit
  dev-luma-tester-lumaui-flow-82566647.service; screenshots/logs at
  /srv/dev/accounts/second/artifacts/dev-luma-tester-lumaui-flow-82566647.
  Native library inputs reused read-only from the existing Calendar-r9 platform
  bundle; only libluma libraries copied into this task's job artifact directory
  because secondary storage is noexec. AppKit source came from this worktree.
  No host library installation. Earlier native-runtime attempts failed due to
  noexec mapping and were corrected; final flow passed.
- Real LumaUI GUI → catalog selection → CLI → QEMU GTK display → pause/resume →
  screenshot → clean child exit passed with a no-OS fixture. Unit
  dev-luma-tester-lumaui-session-cc2dd125.service; artifacts
  /srv/dev/accounts/second/artifacts/dev-luma-tester-lumaui-session-cc2dd125.
  This is launcher integration evidence, not a Luma guest acceptance result.
- Final wheel built and installed into an isolated artifact directory; module
  entry point and desktop file checked. Unit
  dev-luma-tester-package-final-26beb3f3.service; wheel and installation under
  /srv/dev/accounts/second/artifacts/dev-luma-tester-package-final-26beb3f3.
  Wheel SHA-256 d24d76cd4a97441e8ce56f2f9344afdf56c54e5ff682744d0c9040c0ad80ecde.
- `git diff --check` passed. Generated media, disks, keys and packages are excluded
  from Git. All owned validation jobs have exited; no persistent tester service.

## History

- 2026-10-17: read server/project instructions, task ownership, existing twin
  scripts, QEMU documentation and Try Omarchy upstream. Created isolated task
  worktree and branch. Implemented backend, CLI, documentation and package.
- 2026-10-17: user clarified final LumaUI and simple beautiful UX. Replaced generic
  GTK preferences UI with actual shared LumaUI and intent-first flow. Ran actual
  native flow and QEMU integration checks. Guest-image milestone remains explicit.
