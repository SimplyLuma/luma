# Settings panel fallback audit

Audited 2026-10-10 against the maintained Settings51 source with the
Settings52 microphone patch applied. This is a source audit of live controls,
their callbacks, adapter ownership and write qualification. It is not a claim
that every control was clicked on physical hardware. Visibility depends on
installed services, schemas and hardware. The repair uses the existing native adapters and explicit per-key validation.
Delivery and hardware qualification are tracked separately below.

## Cause

There are two action paths to the older panels:

- Some live callbacks call `cc_luma_view_delegated()` before attempting the
  Luma action.
- Other controls call `cc_luma_fixture_write()`. A key without explicit write
  qualification returns `CC_LUMA_WRITE_DELEGATED`, even when its native
  adapter already owns it. `cc_luma_view_report_error()` then delegates.

Both reach `view_delegate()` in `shell/cc-luma-shell.c`, which pins and shows
the corresponding native panel. This explains why a Luma page can change
appearance after an interaction. The separate page-readiness fallback can
also show a native panel when entering an unavailable page; it is not the
same interaction bug.

Qualification was checked against executable registrations, including
adapter-specific and dynamic keys. Historical qualification comments alone
were not treated as current approval.

## Confirmed baseline action paths

Before the repair, these controls had an explicit delegation path or a
native-owned write absent from the qualification list. Conditional controls only
apply when the live page actually offers them.

| Pane | Affected controls or examples | Source evidence in `shell/` |
| --- | --- | --- |
| Keyboard | Add, use, remove or reorder layouts; per-window layouts; alternate-character and Compose keys | `cc-luma-view-keyboard.c` callbacks; `cc-luma-live-keyboard.c`; `cc-luma-live-gsettings.c` (`perWin`) |
| Power | Power mode, low-battery power saver, screen-off timeout, battery sleep and sleep delays, power-button action, battery percentage; charge setting when supported | `cc-luma-view-devices.c`; `cc-luma-live-power.c`; GSettings rows (`pmode`, `charge`, `autoSaver`, `blank`, `susBat`, `susBatD`, `susAcD`, `pbtn`, `pct`) |
| Night Light | Manual start and end times | `cc-luma-view-nightlight.c`; GSettings rows (`nightFrom`, `nightTo`) |
| Appearance | Accent color | `cc-luma-view-appearance.c`; GSettings row (`acc`) |
| Accessibility | Accessibility in Quick Options, larger text, pointer size, screen reader, zoom, zoom options, visual-alert type and keyboard accessibility shortcut | `cc-luma-view-devices.c`; GSettings rows (`a11yMenu`, `large`, `cursor`, `reader`, `zoom`, `zoomF`, `zoomX`, `vAlertHow`, `kbdA11y`) |
| Windows & Workspaces | Tile windows, spacing and edge tiling when Tiling Shell is available | `cc-luma-view-windows.c`; `cc-luma-live-windows.c` owns `tile`, `gap`, `edgeTile` without qualifying them |
| Shelf | Presets and Together/Spread arrangement | `cc-luma-view-shelf.c`; `cc-luma-desktop.c`; `cc-luma-live-shelf.c` (`preset`, `spread`) |
| Sound | Output-device selection, offered output configuration and non-system application volume | `cc-luma-live-sound.c` (`out`, `cfg`, dynamic `lv.<app>`); microphone correction does not approve these |
| Notifications | Individual app switches on this pane | `cc-luma-live-notifications.c` owns dynamic `na.<id>` without qualification; these differ from the qualified Apps-pane notification keys |
| Search | Add a folder; provider enable/order writes through the search model | `cc-luma-view-discovery.c` delegates before the folder chooser; `cc-luma-discovery.c` writes `sa`; no production binding to the alternate SearchLocations view API was found |
| Hotspot / Network | Hotspot enable, name, password and band; wired enable when offered | `cc-luma-live-network.c` owns `hotspot`, `hsName`, `hsPass`, `hsBand`, `eth`; the network qualification helper excludes these |
| Apps | Open, clear cache and uninstall; offered portal-permission switches other than notification/search | `cc-luma-view-application-options.c` callbacks; `cc-luma-live-applications.c` portal ownership loop without qualification |
| Printers | Add, print test page and remove | `cc-luma-view-hardware.c` explicit delegation before native commands |
| Online Accounts | Open an existing account or connect a provider | `cc-luma-view-people.c` (`live_open`, `live_connect`) |
| Users | Add/remove user, password reset and parental-controls action; account type and automatic login when offered | `cc-luma-view-system.c`; `cc-luma-live-users.c` (`users.add`, `users.remove`, `users.reset`, `pType`, `pAuto`, `autoLogin`) |
| Privacy: file history and trash | Clear recent files, empty trash and delete temporary files | `cc-luma-view-privacy.c` explicit live delegation |
| Date & Time | Automatic time, manual time-zone selection, date/seconds/week-number display options | `cc-luma-live-datetime.c` (`autoTime`, `zone`, `date`, `secs`, `wnum`) |
| Sharing / Remote Desktop | Offered media-sharing, file-sharing password policy and remote-control toggle | `cc-luma-live-sharing.c` (`media2`, `fsPass`, `rdCtl`); qualified `fileShare` and `rdTab` are separate |

This table identifies confirmed paths, not every possible unsupported setting.
Some older panels may themselves redirect to a subpage or not perform the
originally requested action. Delegating is not evidence that a change saved.

## Controls excluded from the repair list

- Mouse and Touchpad native controls are already qualified, including the
  secondary-click choices, natural scrolling and speed controls.
- Display selection, scale, refresh rate, orientation, primary display,
  positioning, join/mirror and Apply/Keep/Revert have existing qualification.
  This audit does not establish their behavior on every physical device.
- Settings52 qualifies microphone selection and input volume. Its mapped
  picker test passed, including server readback, reopen and invalid-input
  refusal. This correction ships in Settings53 with Nightly `20261010.3`;
  canonical signed update delivery was verified on 2026-10-10.
- Wi-Fi enable/join/disconnect/forget and its qualified connection options,
  Bluetooth actions, Do Not Disturb and lock-screen notifications are not
  missing the approvals checked here.
- Apps-pane notification and search permissions have dynamic qualification;
  this does not cover Notifications-pane `na.<id>` or portal permissions.
- Dark/light appearance, wallpaper, Night Light enable/schedule mode/warmth,
  qualified workspace controls and several accessibility/privacy controls
  already have approval. Their panes are not uniformly broken.
- Unowned controls hidden in live mode, including notification display
  duration and unsupported pointer/keyboard comfort sliders, are excluded.
  External authentication or file-selection dialogs alone are not a panel
  replacement.

## Repair and acceptance

Track this as `SET-001`. Repair by control or shared adapter family, retaining
the same Luma page while using the actual system API. Do not approve every
owned key or suppress delegation and show fixture success messages: ownership
alone does not prove a working write.

For each corrected action, exercise the visible control with delegation
count zero, verify the owning service reports the requested value, reopen
Settings and check persistence. Test denied, unsupported and disconnected
cases without changing panels or claiming success. For destructive and
privileged actions, verify cancellation and authorization behavior as well.
Package delivery and physical-hardware checks remain separate evidence.

## Repair candidate: Settings53 and Shell110

The shared write-error handler retains the Luma page and displays an error.
Only reviewed, supported native bindings receive write qualification; an
unsupported action remains unavailable instead of reporting fixture success.
Asynchronous adapters report service failures back to that same page.

Native actions cover keyboard layout management, search folders, privacy
cleanup confirmations, application launching/cache cleanup, printer commands,
AccountsService operations, profile pictures and manual date/time. Existing
online accounts and provider enrollment use GOA dialogs parented to Settings.
Application removal explicitly opens the selected app in Depot. The password
action uses the existing PAM/password-strength/keyring dialog through the
Settings host rather than replacing the pane.

Keyboard activation uses Shell110's restricted `SelectInputSource` method.
It admits the Settings bus owner, rejects locked/greeter sessions and selects
only an input source already configured in Shell. Settings53 requires Shell110
so the package solver cannot install that client without its matching API.

Verification includes compiling/linking all 72 production Luma Settings C
sources and the 36-source view regression target; mapped UI failure retention;
56 actual GSettings mappings with fresh-adapter readback and invalid-value
refusal; dynamic notifications and portal permissions; native cache safety,
application/Depot launch and manual-time validation; keyboard/search/privacy
regressions; tiling, Shelf, default applications and private-server Sound
controls. The tiling model also respects a global extension pause without
overriding it. Hotspot and wired bindings use the existing NetworkManager
writer, with input validation and errors retained in the pane. Shell selection
has nine behavior/access-control cases. Tests live
under `tests/gnome-control-center/native-actions` and
`tests/gnome-shell/settings-input-source.js`.

These tests use isolated settings, temporary directories and controlled
services. They do not establish physical microphone/speaker operation,
physical printing, privileged account creation/deletion, external provider
sign-in or input activation in a newly installed Shell session. Those are
runtime qualification boundaries, not claims of completed hardware tests.
The existing page-readiness fallback for an unavailable page remains separate
from the corrected interaction paths.

Package candidates are built from the numbered patch series, with the pane
retention and Shell selection regressions required by their RPM checks.
Publication is tracked separately; source changes do not update an installed
machine or the current website ISO.
