# User-reported regressions

Keep one stable entry for each reported failure. Record the reproduction,
cause, verification, and first shipped version. A source fix or passing unit
test is not a shipped fix; leave delivery pending until the signed update is
available. Add coverage at the boundary the user actually encountered.

## DISP-001 — Display changes have no usable Apply control

- Reported: 2026-10-09, Settings revision ending in `20261009.1`.
- Reproduction: change orientation, scale, or main display in Settings.
  The controls change but the displays do not, and leaving the page loses
  the draft. No usable Apply control appears.
- Cause: the shared action-center fold callback immediately hides the native
  display Apply/Keep bar. Its existing exemption covers fixture display
  countdowns, not the live DisplayConfig adapter's pending transaction.
- Fix: keep the display bar while the native adapter owns pending changes.
  Preserve Apply preview, Keep persistence, and Revert/timeout safety.
- Regression coverage: live GTK controls against a private DisplayConfig
  service. The original view fails the hidden-bar assertion before any native
  write. The corrected view passes desktop and compact runs: mapped Apply/Keep
  clicks, scale/main-display retention after reopening, Cancel without writes,
  and exact Revert. These use the shipped platform107 runtime. Packaged checks
  and delivery remain pending.
- First shipped version: pending.
- Temporary workaround: use `org.projectluma.Displays` and Keep Arrangement.

## UPD-001 — Successive updates have unclear date labels

- Reported: 2026-10-09, successive downloads after the Beta-to-Nightly move;
  an update label includes `20261008`.
- Status: investigating the exact displayed update. The current Nightly's
  build ID is `20261009.4`, while its display name uses the October 8 local
  build date. A Beta recovery followed by Nightly is an expected migration.
  Neither fact proves which update the user saw.
- Follow-up: verify the installed and offered build identities before changing
  update selection or labels. Preserve a later explicit channel choice.
- First shipped correction: not established.

## CONN-001 — Connect lists devices but Sync Now asks to reconnect

- Reported: 2026-10-09, Connect `0.65.0`.
- Reproduction: signed-in Connect shows XPS devices; click Sync Now and receive
  “This computer needs to be connected to Luma Connect again.”
- Status: investigating sync authentication and the app-to-host broker.
  Device listing is not proof that sync is authenticated. The prior release
  did not claim a successful live-account sync test.
- Regression coverage: must exercise the sync request and returned outcome,
  including retained account authentication; a device-list test is insufficient.
- Diagnostic: `scripts/diagnostics/connect-routing.py` checks fixed Hub routes
  using the local registration, without changing it or printing credentials,
  account details, or synced content.
- First shipped correction: pending.
