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
  and exact Revert. These use the shipped platform107 runtime. Settings51's
  normal packaged checks passed, including 22 Meson checks and the scroll-speed
  check. The normal OS gate passed all 47 stages, followed by signed canonical
  client delivery and update-graph verification.
- First shipped version: `1.0.0-nightly.20261009.5`, Settings51. Published
  2026-10-09; physical display confirmation remains a user acceptance check.
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
- Status: client isolation correction shipped; the Hub maintainer confirmed
  the sharing service is absent. Live XPS confirmation remains pending.
  Device listing is not proof that sync is authenticated. The prior release
  did not claim a successful live-account sync test.
- Confirmed client behavior: account status and sync reuse the same saved
  device bearer. Native sync starts with the events request; a 401 becomes
  exit code 3, which the app translates into the generic reconnect message.
  The XPS's actual results are account/events/capabilities/notes/contacts/profile
  HTTP 200, but shared-document listing HTTP 401. This rejects a collaboration
  request while accepting the same device registration elsewhere. The client
  previously aborted healthy personal sync and mislabeled that failure as expired
  registration. The Hub maintainer confirmed that the sharing route and its
  storage/membership service were never implemented: unknown sync paths fell
  through to browser-session authentication. On 2026-10-09 the maintainer changed
  this fallback to authenticate the enrolled device, then return 404 for an
  unoffered service; invalid or revoked credentials still receive 401.
  Preserve registration, report incomplete sharing honestly, and do not let
  that failure prevent healthy services from syncing.
- Client fix: on a sharing 401, recheck account authentication using the same
  saved bearer. A valid account allows separately authorized personal services
  to continue while reporting incomplete sharing. A rejected account still
  requires reconnection. Do not stamp an overall or sharing success checkpoint
  after a sharing failure.
- Regression coverage: real loopback HTTP reproduces documents 401 with account
  200, accepted personal Notes/Contacts requests, preserved credentials/local
  Notes, and unchanged incomplete checkpoints. The old core100 fails these
  cases, including CLI exit 3; core101 passes 17 watch/service tests with HTTP
  resource warnings treated as failures, plus adjacent sync/profile/shared/
  notes/calendar checks. Genuine account, events, and personal request 401s
  remain authorization failures. Packaged delivery passed the normal native
  checks, all 47 OS gate stages, and trusted canonical download/signature,
  complete closure and filesystem checks. Live XPS confirmation remains
  pending. A further real-HTTP regression confirms the existing client accepts
  a sharing-list 404 as unsupported, completes personal Notes/Contacts sync,
  and preserves registration and local notes; all 18 watch/service tests pass.
- Remaining sharing work: implement the authenticated Hub document service and
  validate it with separate accounts and a read-only member. The current client
  skips an unsupported service but reports zero documents refreshed, and a
  shared-only event can advance its sharing checkpoint despite that skip.
  Correct this status/checkpoint distinction before claiming shared sync works.
- Diagnostic: `scripts/diagnostics/connect-routing.py` checks fixed Hub routes
  using the local registration, without changing it or printing credentials,
  account details, or synced content.
- First shipped client correction: Nightly `20261009.6`, version
  `1.0.0-nightly.20261009.6`, commit
  `b022c0bf2a6c65d74fb842ba007bf92df0b6f5e2e45daa5fc7ac6045a16a4c07`.
  Published 2026-10-09. Shared syncing is not claimed repaired.

## MON-001 — Monitor cannot read system activity on the XPS

- Reported: 2026-10-09 on a newly installed Dell XPS.
- Reproduction: open Monitor; it says it couldn't read system activity while
  CPU activity continues showing Loading.
- Status: investigating the sampler, host broker and error presentation.
  No cause or successful physical-device test has been established.
- Required behavior: show real measured activity when available and a clear
  unavailable state after a failed read, with a recoverable retry. Do not
  present a failed sample as an indefinitely pending measurement.
- First shipped correction: pending.
