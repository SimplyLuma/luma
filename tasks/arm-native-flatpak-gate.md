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

## 2026-10-26 UTC — historical retired-app cache admission

Added explicit `historical=False` to the maintained baseline validator. Normal
OS/install callers retain the retired-app rejection. Only pinned producer reuse
passes `historical=True`, allowing a prior retired app in the old cache while
all OPTIONAL/schema/key/role/ref/file/hash guards remain unchanged. Production
reuse still selects only the current authenticated shipping refs and exact
commits; retired cache entries are never copied merely because they exist.

Actual final `dev-arm-offline-historical-cache-final-2315822c` exited0: seven
root-owned real bundle/custody tests pass, including a prior Displays cache
accepted only historically, the same manifest rejected by normal default
admission, and historical rejection of optional Write. Earlier d4711bfd also
passed before the extra optional-app assertion. Python compilation and diff
checks pass. No protected actual cache, source old baseline, root active output
or producer job was changed by this helper.

## 2026-10-26 UTC — host namespace fixture candidate

Run 40249388808 failed before Flatpak installation: Bubblewrap reported
`setting up uid map: Permission denied` in namespace-preflight.stderr. Its
signed-inputs.json had already passed full archive hashing. Private downloaded
evidence is under native-flatpak-ci-40249388808/artifact in this account's
arm-desktop experiment directory. No application runtime pass is claimed.

The native workflow now runs on its Ubuntu ARM host and explicitly creates the
same pinned Fedora container after loading a unique temporary AppArmor profile.
Ubuntu's documented `flags=(default_allow)` plus `userns` rule addresses host
mediation that plain `apparmor=unconfined` does not grant to unprivileged users.
No host sysctl changes, Docker seccomp changes, or Flatpak/Chromium overrides
are introduced. The container retains the previously authorized SYS_ADMIN.
A numeric UID 1100 Bubblewrap preflight runs before the signed archive download;
the existing normal-user preflight and every runtime assertion remain. Cleanup
removes only this run's container/profile and gives the host artifact action
read access to fresh evidence. No GitHub token is forwarded into the container.

Primary rationale: https://ubuntu.com/blog/ubuntu-23-10-restricted-unprivileged-user-namespaces

Focused Bash syntax and native host workflow checks run in admitted unit
`dev-arm-native-container-fixture-checks-1d9e89d1`; actual policy loading and
UID-map success require a fresh Ubuntu ARM CI run. No dispatch or host policy
mutation was performed on the shared Fedora server.

## 2026-10-26 UTC — signed Flatpak failure diagnostics

Run 40251312020 completed normal signed four-ref installation and mapped Viola,
but failed waiting for trusted requested-page phase 0. Its final GTK/SHM viewport
is 883×579 with valid native identity and active window; active URL remains
about:blank and no requested-page GET reached the server. Earlier engine frame
sizes are startup transitions. Source and argv inspection confirms the normal
shipping wrapper forwards --start-url. The complete engine log contains shared
image errors and unrelated GCM HTTP responses; neither establishes the cause.
The encrypted default/login keyring is unlocked. A sandbox Secret portal issue
remains a hypothesis, not a demonstrated application defect.

The diagnostic fixture now preserves the original failure while allowing up to
15 seconds for an actual mapped-window close and host integration-report drain,
then bounded TERM/KILL fallback. It records cleanup outcome without changing
acceptance. A delayed read-only metadata helper records public portal/backend
owners, Secret interface version/introspection and installed portal selection
configuration. It never retrieves a secret, item, token or permission database.
App argv, permissions, source, native identity and all mechanism guards stay
unchanged. The existing report collector retains the resulting integration
report and engine-exit/request-timeout evidence.

Focused source check unit dev-arm-signed-portal-diagnostic-source-r2-f56ebeef
exited 0 with Bash, JavaScript and Python syntax and diff checks passing. It used
a 1 GiB reservation. The first default-sized reservation was refused by
admission before running; no test result was inferred from that refusal.
Actual new cleanup/metadata behavior still requires the next native CI run.

## 2026-10-26 UTC — actual signed runtime observer correction

Run 40252402497 reached the unique requested page and posted DOM phase 0. Its
failure is the observer's incorrect runtime string comparison, before pixel
observation. Actual normal close succeeded without signals: integration errors
empty, command timeouts zero, engine exit 0 and pending leases zero. Public
Secret version 1 and the normal GNOME keyring backend were present. Complete
artifacts are in second-account experiments/arm-desktop/
native-flatpak-diagnostic-40252402497/artifact. This is progress, not acceptance.

Flatpak 1.18.4 common/flatpak-run.c writes Application.runtime using
flatpak_decomposed_get_ref(), including the runtime/ prefix, rather than the
short preference used in an app manifest. The observer now uses the real GI
Flatpak.Ref parser, requires the exact canonical Platform/aarch64/44 runtime
ref and checks its commit against both the admitted deployment and signed ref
map. All mounted-deployment, PID ancestry, broker, argv, environment, native
frame/pixel/input/resize/close checks remain in force.

The signed launcher now passes its URL positionally, as the maintained desktop
entry's Exec=viola-browser %U specifies. The shipping wrapper forwards it and
startup_navigation.py routes explicit URLs through Chromium's existing singleton
URI mechanism. This avoids exercising the custom --start-url palette path;
its earlier intermittent page failure remains an unproven timing hypothesis.
The observer requires the corresponding exact normal host argv. Native RPM
launch arguments remain the same.

Actual dev-arm-signed-runtime-ref-parser-77706ac1 exited 0. Three tests use the
real GI parser and cover exact selected commit admission plus wrong kind/name/
architecture/branch, short preference, changed commit and disagreeing signed
map rejection. JavaScript syntax and diff checks passed. Next native signed CI
is still required; no product or engine rebuild was performed.

Primary source: https://github.com/flatpak/flatpak/blob/1.18.4/common/flatpak-run.c
