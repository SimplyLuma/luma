# Task: arm-native-flatpak-gate

- Status: active
- Owner: luma-dev-2; Codex /root/native_browser_environment
- Updated: 2026-10-25T23:38:42.055995+00:00
- Project: luma-os
- Branch and last useful commit: agent/1003/arm-native-flatpak-gate; 6f8bbafd149406cd757f8171f902aafb8bb36e79
- Worktree: /srv/dev/worktrees/luma-os/arm-native-flatpak-gate
- Scope and expected result: native ARM signed Flatpak browser acceptance using the existing real GNOME mechanism proof, without rebuilding or changing product permissions.
- Files / interfaces owned by this task: browser runtime workflow/probe, fixture actor and identity observer, signed selected-ref intake/installer.
- Related tasks and integration owner: root arm-desktop integrates; arm_publication_r6 owns selected signed-ref archive export/transport.

## Current handoff

Ready for root review/cherry-pick. Optional `signed_input_url` and
`signed_input_sha256` workflow inputs accept only the content-addressed
`https://dl.simplyluma.com/inputs/depot-build/SHA/runtime-input.tar` transport.
The existing exporter1126 schema and original signed summary are preserved.
Selected refs are Viola aarch64/beta and Platform, GL.default, Locale aarch64/44.
Publication agent owns export/upload; this task did neither.

The installer imports all four exact signed commits through a fresh normal
user Flatpak installation (runtimes first, then app with normal dependency
checks), verifies the original signed summary and maintained
signature fingerprint/ref bindings, restores the maintained official `luma`
remote URL, rejects all permission overrides, and compares the actual engine
and every native38 shipping host file (excluding generated PYC) with the signed
app's files. Extra signed app source files are retained. Only normal
`flatpak run --user app/com.rhyme.viola/aarch64/beta --start-url URL` launches
acceptance; there are no command/environment/permission overrides.

The shared compositor/DOM/pixel/input/resize/minimize/close proof is unchanged.
The observer binds the actual window PID to the running Flatpak instance,
normal shipping argv/environment, exact signed mounted deployment/runtime,
maintained broker authentication and unchanged native SHM identity guards.
No signed Flatpak runtime pass has been observed yet; first native CI remains
necessary after the selected signed input is ready. No OS qualification or
publication claim.

## Validation and artifacts

- `dev-arm-native-flatpak-source-checks-58688aae`: exit0; four real archive
  intake tests pass, including traversal/link/unselected-ref/changed-inventory/
  changed-source/wrong-digest rejection. Synthetic signatures only exercise
  transport admission and never claim authentication.
- `dev-arm-native-flatpak-fixture-regression-27085d40`: exit0; three existing
  real HTTP/PNG regression tests pass, including strict all20 palette samples.
- Bash syntax, changed Python compilation, actor JavaScript module syntax and
  `git diff --check` pass. These are source checks, not installed acceptance.
- No active task jobs. No CI dispatch, export, upload or public metadata change.

## Dated history

- 2026-10-25 UTC: implemented the reusable signed Flatpak mode from root
  6f8bbafd149406cd757f8171f902aafb8bb36e79. Native RPM default remains available.

## 2026-10-26 UTC — pinned offline bundle reuse tooling

Added optional `--reuse-baseline DIR --reuse-manifest-sha256 SHA` to the maintained
baseline producer. The manifest pin must come from the prior protected verified
baseline admission. The maintained OS validator checks the old structure,
shipping/role/key inventories and file protection with `hash_bundles=False`.
Current signed summary and every current commit still pass the existing
independent GPG admission before reuse is considered. Key bytes must match the
currently authenticated site key exactly. Only identical ref+commit candidates
skip the old recursive OSTree inventory and bundle recompression; new/changed
refs use the normal producer. Reused bodies are copied exclusively to a fresh
output, fully hashed while reading, independently fully read back, checked by
actual Flatpak.BundleRef and guarded against source changes. New output shipping
and role manifests continue to come from the current source selection.

Actual `dev-arm-offline-baseline-reuse-checks-r2-f07e4fb9` exited0: six root-owned
boundary tests with a real tiny Flatpak bundle cover exact copy/readback,
changed-commit refusal, same-size corruption, changed/pin-mismatched manifest,
foreign current key and actual bundle identity mismatch. The tiny bundle is
unsigned and tests custody/identity only; current normal GPG admission is
unchanged. Initial58daa624 exited2 before tests because the administrator wrapper
changes cwd; absolute test path corrected that harness issue. Python compilation
and diff checks pass. No root active baseline producer/output or old bundle was
touched; no whole baseline run was launched by this helper.
