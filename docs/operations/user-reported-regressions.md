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
- Status: investigating the exact displayed update. Nightly
  build `20261009.4` uses the October 8 local build date in its display name.
  A Beta recovery followed by Nightly is an expected migration.
  Neither fact proves which update the user saw.
- Follow-up: verify the installed and offered build identities before changing
  update selection or labels. Preserve a later explicit channel choice.
- First shipped correction: not established.

## TILE-001 — Tiling disappears from Quick Options after updating

- Reported: 2026-10-10, with the installed OS reporting Nightly `20261009.6`.
- Confirmed session cause: the XPS reports `disable-user-extensions=true`.
  Both Tiling Toggle and Tiling Shell are installed and listed as enabled,
  but their runtime state is `INITIALIZED`, with `Enabled: No`. The per-extension
  disabled list is empty. The global extension pause prevents the control loading.
- Possible trigger: the shipped Shell has a conditional failure-recovery service
  that sets this flag when extensions were marked as a likely cause of failure.
  Its presence does not establish that it ran on the XPS; check that service's
  user journal before attributing the change to a crash.
- Immediate recovery offered: explicitly set `org.gnome.shell`
  `disable-user-extensions` to `false`, then reopen Quick Options. This retains
  the user's extension lists and tiling preferences. Await actual user confirmation.
- Required behavior: keep the first-party Tiling control discoverable when
  automatic window tiling is off or extensions are paused; Settings must reflect
  actual runtime availability. Preserve deliberate third-party extension choices
  and crash recovery rather than silently resetting all preferences.
- Follow-up: verify control visibility after recovery and investigate a first-party
  control that does not disappear with optional extensions. Do not rebuild the
  frozen release merely on an assumed rendering failure.
- First shipped correction: pending; no new package fix is claimed.

## CONN-001 — Connect lists devices but Sync Now asks to reconnect

- Reported: 2026-10-09, Connect `0.65.0`.
- Reproduction: signed-in Connect shows XPS devices; click Sync Now and receive
  “This computer needs to be connected to Luma Connect again.”
- Status: client isolation correction shipped; the Hub maintainer confirmed
  the sharing service is absent. The user subsequently reports 27 Notes and
  Contacts synced on the XPS. This confirms personal progress, not shared
  document collaboration. Earlier Hub retry notices require their normal
  backoff; a Photos error must not erase another service's successful sync.
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
- Cause: the Monitor user service's `ProtectSystem`, `ProtectHome` and
  `PrivateTmp` settings create a private user namespace. Its signed Flatpak
  caller occupies a sibling namespace, so reading the caller's live
  `/proc/PID/root/.flatpak-info` identity fails with permission denied before
  any system activity is sampled. The same user outside that service can
  verify the caller. The original UI leaves the failed read showing Loading.
- Correction prepared: Monitor 17 retains unprivileged execution, process and
  resource restrictions, and the complete signed deployment/lifecycle checks;
  it omits those three namespace-creating filesystem options. The client shows
  an unavailable or explicitly stale reading, retries and restores measured
  activity when a sample succeeds, without repeated error toasts.
- Evidence: a fresh installed Nightly `20261009.6` guest with SELinux enforcing,
  Monitor 16, Installer 69 and SDK 107 reproduces the exact refusal on the
  original service. Changing only its service configuration restores actual
  signed Monitor CPU and memory samples; an actual signed wrong-app caller
  with transport permission and an unsigned caller remain refused. All 88
  headless unit tests pass against that installed SDK. The existing GTK
  runtime lane repeats the error/recovery tests on actual labels. These guest
  results do not claim a successful physical XPS retest.
- Required behavior: show real measured activity when available and a clear
  unavailable state after a failed read, with a recoverable retry. Do not
  present a failed sample as an indefinitely pending measurement.
- First shipped OS correction: Nightly `20261010.1`, version
  `1.0.0-nightly.20261010.1`, commit
  `2365d712dba08e950abeff1d1d6d10cf0ac73dcec602b790051af44dfb5712d5`.
  Published and verified 2026-10-10. The actual installed production service
  serves signed Monitor samples and refuses wrong-app and unsigned callers.
  The full 47-stage release gate and fresh canonical signed download passed.
  The independent app feed and the new ISO were also published and verified
  on 2026-10-10. The actual public app payload passed signature and filesystem
  checks, and the website's primary download selects this ISO. A physical
  XPS retest has not been reported.

## PHOTO-001 — Photos rejects an existing shared-library access grant

- Reported: 2026-10-10. Photos displays an unactionable access warning;
  Connect reports two Photos while Notes and Contacts have synced.
- Cause: the explicit-mount check compares the home alias and canonical home
  path as different strings. The existing narrow Flatpak grant is present,
  but its mount target uses the canonical path.
- Correction prepared: resolve both paths before comparing their exact
  library mount. A parent home mount alone still does not grant access.
  Preserve the existing library and the shared Photos/Camera catalog.
- Regression coverage: the installed signed Photos app reproduced the
  warning with the grant present. The old code fails the granted-alias case;
  the corrected backend passes all 19 unit tests, including denial without
  an explicit library mount, plus four adjacent Camera media tests. A
  committed installed-sandbox test checks both signed apps with the grant
  and a command-local revocation without opening SQLite or changing
  persistent permissions. All four new-package installed-sandbox cases passed.
- Count interpretation: two reported Photos alone does not establish how
  many items the user's library should contain; verify the accessible
  catalog before treating that number as a sync loss.
- First shipped OS correction: Nightly `20261010.1`, version
  `1.0.0-nightly.20261010.1`, commit
  `2365d712dba08e950abeff1d1d6d10cf0ac73dcec602b790051af44dfb5712d5`.
  Published and verified 2026-10-10 with the corrected Photos and Camera
  offline bundles and Core 102. The independent Photos and Camera app updates
  and the new ISO were also published and verified on 2026-10-10. Actual
  public payloads passed signature and filesystem checks; this does not
  establish the user's expected photo count.

## CONN-002 — Music Servers remains waiting after personal sync succeeds

- Reported: 2026-10-10 after Notes, Contacts and Photos report synced.
- Confirmed code behavior: Music Servers explicitly skips Flatpak Tide in
  the current client. A native Tide without a network-capable source library
  is also skipped. Services execute independently; Photos is not a queue
  that must finish before Music Servers starts.
- Follow-up: verify the installed Tide identity and network-source support,
  then expose an accurate unavailable or skipped state and implement the
  supported synchronization path without bypassing Tide's model or keyring.
  Do not report a skipped operation as synced or request a new registration.
- First shipped correction: pending.

## AUDIO-001 — Tiger Lake speaker output is silent

- Reported: 2026-10-10. Reporter identifies an Alliwava Mini PC with Intel
  Core i7-11390H, 32 GB DDR4 and 512 GB storage. The later diagnostic identifies
  `ALL-H90-Defaultstring`, kernel `7.2.8-200.fc44.x86_64`, and booted Nightly
  `20261010.1` (`2365d712dba08e950abeff1d1d6d10cf0ac73dcec602b790051af44dfb5712d5`).
  Headphones work; selecting speakers
  does not help. ALSA Mixer displays the
  speaker control at `0–0%`. The reporter says audio works in Ubuntu, Debian
  and Kali on the same machine.
- Status: hardware diagnosis pending. Tiger Lake alone does not identify the
  speaker codec, amplifier, firmware topology or matching UCM profile.
- Confirmed card and route: `sof-essx8336`; PipeWire 1.6.9 lists the analog
  Speakers sink as selected at 100%. HDMI/DisplayPort outputs are separate,
  including a Sony TV at 40%. No active audio stream is present in the supplied
  snapshot. The HD-audio codec dump identifies Intel Tigerlake HDMI, not the
  separate I2C ES8336 analog codec. This establishes card recognition and the
  selected route, not successful analog playback.
- Upstream source audit: the ES8336 machine driver exposes Speaker as a DAPM
  pin switch; the ALSA UCM speaker profile uses Headphone Mixer and DAC for
  volume. A `0–0%` Speaker display alone does not prove a missing gain control.
  GPIO amplifier enable, jack detection, mixer state and actual output connection
  remain to be checked before choosing a correction. Relevant primary sources:
  [machine driver](https://github.com/torvalds/linux/blob/master/sound/soc/intel/boards/sof_es8336.c)
  and [UCM profile](https://github.com/alsa-project/alsa-ucm-conf/blob/master/ucm2/Intel/sof-essx8336/HiFi.conf).
- Package audit: the actual `20261010.1` image inventory includes
  `alsa-sof-firmware 2025.12.2`, `alsa-ucm 1.2.16.1`, kernel `7.2.8`,
  PipeWire `1.6.9` and WirePlumber `0.5.18`. This does not establish which
  version the reporter installed or prove a working speaker path.
- Exact-image audit: the linked UCM HiFi and BootSequence match upstream;
  the actual ES8336 kernel module contains the expected delayed speaker-GPIO
  path. Shipped module settings contain no Luma SOF override, and output policy
  only sets default-route metadata. No applicable Alliwava/H90 upstream DMI
  quirk or safe correction is established by this audit.
- Follow-up: the user declined further reporter diagnostics. Keep the report
  open without another command checklist. Actual speaker connection, amplifier
  GPIO/jack polarity and persisted mixer state remain unknown. Do not apply a
  global DSP-driver override or another machine's GPIO quirk.
- First shipped correction: not established.

## AUDIO-002 — Microphone selection replaces Luma's Sound panel

- Reported: 2026-10-10, Dell XPS DA14260. Selecting the input dropdown opens
  the older native Sound panel. The user subsequently confirmed that the
  microphone works, with low volume; no capture failure is claimed.
- Cause: the live Gvc adapter owns microphone selection and volume, but the
  verified-write gate does not approve those keys. A picker write returns
  `CC_LUMA_WRITE_DELEGATED`, which deliberately replaces the Luma view.
  Input labels also use only the port description, such as “Microphones”,
  although the adapter already reads the hardware origin.
- Source correction: qualify only input selection and volume in the existing
  sound adapter; preserve disconnected-device refusal and readiness checks.
  Include a distinct hardware origin in input names. Other unqualified
  controls are not silently enabled.
- Verification: the original adapter fails the mapped-picker assertion with
  one delegation. The corrected adapter selects a second source on a private
  real audio server without delegation, sets its volume to 42%, unmutes it,
  retains selection and volume across adapter/view recreation, and rejects
  an input not offered by the picker without changing the default source.
  This verifies the Gvc protocol and UI boundary, not physical microphone
  capture or the separate Tiger Lake speaker issue.
- Regression runner: `tests/gnome-control-center/sound-input/run.sh`.
- Delivery: included in the Settings53 repair candidate; publication pending.
  First shipped correction remains pending.

## SET-001 — Settings actions replace Luma panes with older native panels

- Reported: 2026-10-10 after the Sound input-picker report; earlier reports
  included Mouse/Touchpad and Displays.
- Source audit: the issue extends beyond Sound. Explicit delegation callbacks
  and the shared unqualified-write fallback still route multiple controls to
  older native panes. These paths are recorded by control in
  [the Settings audit](settings-panel-fallback-audit.md), with already qualified
  controls and hidden/unowned controls excluded.
- Scope of evidence: maintained Settings51 source plus the Settings52 input
  correction; not a physical-device click-through of every pane.
- Source correction: Settings53 retains the Luma pane on refused writes and
  asynchronous errors, qualifies reviewed native bindings and replaces explicit
  action delegation with native handlers. Shell110 supplies restricted input
  source selection; Settings requires that matching package.
- Verification: mapped pane-retention regression, native adapter readback and
  refusal checks, action/cancellation tests and full production compile/link.
  See the audit for precise coverage and runtime qualification limits.
- First complete shipped correction: pending. The microphone-specific source
  correction and test are tracked separately as AUDIO-002.

## SESSION-001 — Dell XPS loses the session while unattended

- Reported: 2026-10-10, Dell XPS DA14260. After walking away, the user
  returns to the login screen; logging in starts a session without the
  previously open applications. Frequency is described as every unattended
  interval. This is not established to be an ordinary screen lock.
- User confirmed that this also happens plugged in with the lid open; lid
  closure is not required. Idle-triggered locking, blanking or sleep have not
  been excluded by this detail alone.
- Installed identity: Nightly `20261010.1`, Shell109 and Mutter13. The
  supplied coredump list records repeated Shell SIGABRTs; Viola SIGTRAPs often
  follow one second later. The fatal main-thread trace aborts in
  `clutter_actor_destroy_all_children`, after two nested actor-destroy calls.
- Confirmed cause: Studio's adopted Tiling toggle destroys its parent wrapper
  from its own destroy callback while it is still attached. Clutter's recursive
  child destruction then cannot remove that already-destroying child and aborts
  on `n_children < prev_n_children`. Lock mode disables this extension, invoking
  exactly that callback. This is separate from nonfatal disposed-Settings
  warnings seen in the replacement greeter session.
- Correction: Shell112 detaches the destroying toggle before destroying its
  wrapper and clears its wrapper reference. Three lock/greeter Settings owners
  also disconnect their tracked callbacks before disposing Settings.
- Regression evidence: the original production callback, full adoption method,
  actual packaged extension disable, and actual lock-mode extension disable all
  reproduce the same fatal assertion under normal logging. The completed
  Shell112 RPM passes 10 actual extension disable/re-enable cycles and 20
  ScreenShield lock/unlock cycles with Tiling Shell24 and Tiling Toggle12.
  Those packaged runs use no production UI module overlays. The isolated
  headless fixture simulates GDM eligibility and unlock completion; it does not
  claim a physical XPS authentication, GPU, suspend or idle-duration retest.
  The normal package build and both native tracked-Settings regressions pass.
- Regression runner: `tests/gnome-shell/run-studio-tiling-lifecycle.sh`. Run
  only in an explicitly disposable environment with matching Shell/Mutter and
  the actual packaged extensions. Preserve the old fatal and corrected evidence
  privately; do not put user core files or account data in source control.
- First shipped correction: pending; built and qualified Shell112 has not yet
  been published in the signed OS update feed.

## NOTIF-001 — Outside clicks in applications leave Notifications open

- Reported: 2026-10-10. Clicking the desktop closes the expanded notification
  panel, but clicking inside an application does not. Applies to both an empty
  panel and one containing notifications.
- Cause: the listener only captures stage events. Client-window input bypasses
  that path without a Shell grab. The earlier callback test injected stage
  events and did not exercise real application routing.
- Correction: Shell111 routes input through the notification actor's modal
  grab and handles outside coordinates on that actor. Closing disconnects
  capture, releases the grab and restores focus. Closing does not dismiss
  notification records; inside interactions remain available.
- Verification: the current Shell110 fails the real Wayland application-click
  regression. The candidate passes native mouse/touch routing for empty and
  populated panels, right-click, desktop dismissal, Escape, listener/grab
  cleanup and subsequent application input. Reopening through the nub is
  checked using real mouse and touch input.
- Regressions: `tests/gnome-shell/notification-lip-outside-click.js` and
  `tests/gnome-shell/run-notification-outside-native.sh`. The native runner
  requires an explicitly disposable environment with matching Shell/Mutter.
- Publication: pending; an installed machine needs the matching OS update.
