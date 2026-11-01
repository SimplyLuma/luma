# Shared Mobile shell migration handoff

The current Titan development source is on
`codex/project-titan-usb-20261029` in `SimplyLuma/luma`. Integrate this branch
onto the receiving device's current source cohort before building. This is the
shared Luma GNOME Shell with Mobile presentation, using the Desktop/Tablet
services and native controls. The old GNOME Mobile 48 bake-off and Phosh renderer
are separate historical paths.

If a checkout cannot find these sources, inspect the development ref explicitly;
they are not yet integrated into `main`. This does not switch or reset the
receiving agent's worktree:

```sh
git fetch origin refs/heads/codex/project-titan-usb-20261029:refs/remotes/origin/codex/project-titan-usb-20261029
git show origin/codex/project-titan-usb-20261029:src/luma-shell-presentation/presentation.js
```

## App startup

The prepared GTK runtime, native launch frontend, bounded native Settings/Filer
preparation and immutable-image Python caches are maintained in the shared
platform. Carry the matching package cohort, not just the shell JS. See
[Mobile application startup](mobile-app-startup.md) for architecture, confinement
boundaries, measured first-frame timings and remaining qualification gates.

## Widget resizing

Shell source candidate 141 adds shared Mobile/Tablet widget resizing in Home edit
mode. A selected widget exposes a corner handle; nearby items move aside while
preserving whole-cell spans. Release saves, Escape cancels, and provider size
limits apply. Keyboard resizing and reduced motion use the same transaction.
The Titan passed 12 native checks and the owner approved the physical candidate.
The source and regression tests are on this branch; other devices require a
matching package build. The Titan currently uses a source-backed override on
installed Shell 139, not a built 141 package.

## Source to carry over

| Area | Maintained source |
| --- | --- |
| Home, dock, paging, widgets, shared search, gestures and app motion | `src/luma-shell-presentation/` |
| Compact login, automatic hardware-keyboard/OSK behavior | `patches/gnome-shell/0290-compact-login-action-bar.patch`, `0291-shared-handheld-startup-and-input.patch` |
| Notification island, full-screen Mobile tray, lock privacy and touch target | `patches/gnome-shell/0292-display-exclusions-and-notification-touch.patch`, `0293-mobile-lock-notification-island.patch`, `0294-notification-dismissal-policy.patch` |
| Shared window geometry, safe areas and app action bars | `src/luma-platform/appkit/luma_appkit/`, `src/luma-platform/ui/` and maintained app/Mutter patches |
| Live Weather/Calendar models and background refresh | `src/luma-home-widgets/`, `src/prairie-core/prairie_apps/weather*.py` |
| Maintained native Terminal and shared command action bar | `src/luma-terminal/` |
| Mode/state service | `src/luma-shell-state/` |
| Device display exclusions (schema 2 lists several panels by connector) | `config/mobile/display-geometry/` |
| Keycap on-screen keyboard | `patches/gnome-shell/0294-keycap-keyboard.patch`, `src/luma-shell-presentation/keycap*`, `keycap` tokens; see `docs/mobile/keycap-keyboard.md` |
| No window controls in Tablet and Mobile | `presentation_button_layout` in `src/luma-shell-state/`, LumaUI window controls |
| Input, power and appearance defaults | `config/mobile/luma-shell-handheld.dconf` |

The converged nightly source pins Shell `1.luma.140`,
Mutter `1.luma.17.keycap20261031.1`, developer platform `1.luma.121`,
core apps `1.luma.126`, Tide `2.luma.38` and widgets `1.luma.3`. These identify requested
builds, not published or qualified RPMs. Preserve newer work already present in
the receiving branch/device; the Titan's private development install includes
newer unrelated platform UI work, so do not downgrade an installed package tuple
just to match an older base.

Release counters from different device branches do not establish compatibility.
Keep native callers and the shared toolkit in a checked cohort, and verify their
dynamic symbols when combining builds. The Titan Settings70/eSIM binary required
legacy activation-code/QR symbols absent from Platform121; matching current
Settings59 loads with that toolkit. Keeping the higher-numbered binary caused
Settings to exit before opening.

## Moving another phone from Phosh

1. Keep that phone's working kernel, boot chain, firmware, modem, audio, power
   services and recovery access. Record the installed package/session tuple and
   retain its usable deployment. This branch does not qualify other hardware.
2. Build the current maintained Shell/Mutter, developer platform, core apps and
   widget sources for the target architecture, with matching GJS/GI dependencies.
   Entry points are `scripts/packages/build-gnome-shell.sh`, `build-mutter.sh`,
   `build-luma-developer-platform.sh`, `build-prairie-core-apps.sh` and
   `build-luma-home-widgets.sh`. Check each builder's architecture support;
   never relabel an x86 package as ARM. The noarch shell-state service must also
   be present. For the shared Terminal action bar, build/install the maintained
   `luma-terminal` with `scripts/packages/build-luma-terminal.sh` and its
   `org.projectluma.Terminal.desktop` launcher; the Titan replaced legacy Ptyxis
   through a local launcher alias. The Shell builder installs the presentation sources and all
   registered native patches, including 0293.
3. Use a normal Wayland GNOME session with GDM where that device supports it.
   The existing `scripts/mobile/luma-shell-session` and
   `config/mobile/shell-bakeoff/greetd-luma.toml` describe the older greetd native
   bring-up path; they are not a universal replacement for GDM. Avoid running
   two compositors/display managers on the same seat. Start with a working
   authenticated session before changing the default login path.
4. Apply the shared handheld defaults and select Mobile through the existing
   presentation service. Native keyboard capabilities govern automatic OSK;
   do not disable touch keyboards globally. Keep per-device camera cutouts in
   display metadata, never in app-specific offsets.
5. Verify touch, physical keys/OSK, password input, blank/wake, app work areas,
   Home rearrangement, search/switcher, notification and Quick Options pulls,
   locked notification privacy, Weather updates and the device's phone services.
   Keep device evidence and recovery material outside Git.

The historical `config/mobile/ui-packages.txt` still describes a Phosh image.
Pushing this branch does not automatically convert existing phone images or
publish an OTA. The migration agent must update the receiving device's own
composition/session profile and build/install the matched native cohort.

## Current behavior and remaining work

Mobile uses the shared Home and master search. On the Titan's 360 by 400 logical
screen, Home has five columns and four rows plus the dock; each widget spans
whole cells, usually two rows. Density follows usable geometry, not a Titan ID.
Notifications are pulled from the top island; Quick Options is pulled from the
right status area. Full pages use the dock palette's opaque treatment for readable text.
Both fill the screen, scroll internally, follow/reverse with
the finger and snap on release. Mobile lock uses the same notification anatomy,
with lock visibility/redaction rules and authentication before app activation.
Desktop/Tablet lock presentation remains separate. Mobile bottom-edge taps reach
application controls; Home and the app switcher require an actual upward drag.
Home and right-edge Back observe the native stage instead of using a reactive
button over the app. Back waits for a deliberate inward horizontal drag; ordinary
scrolls and taps remain in the app. Tablet retains its explicit Home control.

Home's interrupted-settle jump is corrected, but steady page animation remains
around 30 painted FPS on the Titan's current 60 Hz mode. Hardware acceleration
is working; the current kernel exposes no faster mode or VRR range. A brief GPU
clock comparison did not fix cadence and was reverted. Do not force permanent
high clocks or claim 60 FPS is solved. Continue compositor/presentation profiling
on the receiving hardware. Panel masks/timelines exist only during interaction;
Weather uses cache TTLs and a 15-minute non-waking timer, not a continuous loop.

Validation and physical-device evidence are recorded in
`tasks/project-titan-usb-20261029.md`. Private Titan resource overrides, root
keys, user data, raw backups, captures, generated libraries/RPMs and diagnostic
scripts are intentionally excluded. All product implementation belongs in the
maintained source paths above, rather than copying the private override tree.

Nightly integration also includes the e970f249b Keycap press/lock-screen follow-up; the combined Shell package is 1.luma.134.nightly20261030.1.

Native Tide/Connect source synchronization repairs and the matching Tide build
are documented in [Tide server sync](tide-connect-sync.md).

Titan boot health: the older board check requires greetd, Phoc and Phosh.
Migrating that phone to GDM/GNOME also requires a session-aware check, or the
initramfs attempt counter can return it to the pinned older deployment.
`scripts/mobile/session-healthcheck.py` preserves required update checks and
validates the selected display manager and its corresponding shell. The
development Titan uses this helper through a scoped
`greenboot-healthcheck.service` drop-in. Retain the existing success/bless
service and fallback mechanism; do not mark a candidate successful before its
health check passes. This is a Titan operator integration, not a replacement
for other boards' boot backends.


The restored native GDM selection has now passed a fresh reboot and required
update/session health checks. Physical-keyboard login and the shared Home work.
A legacy explicit `screen-keyboard-enabled=true` user setting was cleared on
the Titan; maintained physical-keyboard detection and touch-only/accessibility
behavior remain unchanged. Keep the shared false default when migrating old
Phosh configurations; do not force-enable the OSK in recovery scripts.

The MT6878 audio PLL correction is maintained separately under
`patches/linux-mt6878/0065-clk-mediatek-mt6878-preserve-independent-PLL-gates.patch`.
It is hardware-specific and belongs after the device branch's preceding kernel
series. Its full kernel has booted on the owner's Titan, and the owner confirms
Tide playback is smooth. Doom/call-path listening checks remain untested. It is not a desktop/tablet audio-policy change or OS release.


Owner has accepted the current Titan playback and requested retaining it.
Individual physical loudspeaker/receiver isolation was not completed; do not
interpret mixer labels or the manual diagram as independent acoustic evidence.
Keep this limitation with the source/build handoff. The tested corrected kernel
and rollback image are retained privately; no general release was published.

Touch wake candidate: kernel patch0066 and the optional shared
`scripts/mobile/touch-wake-policy.py` / `config/mobile/luma-touch-wake-policy.service`
use FT3383 hardware gesture detection and compositor display-power signals.
Device profiles opt in with `wakeup-source`; unsupported devices are unchanged.
The driver reports KEY_WAKEUP on a separate input device and retains low-power
mode until actual unblank. Shared handheld `powerButton.js` explicitly handles
XF86WakeUp as wake-only, including while locked: relying on ordinary input idle
activity was inconsistent after manual display blank. Physical recognition was
observed, but the initial implementation failed to unblank. Three consecutive
injected events now pass with the revised shell handler and keep the lock.
The owner confirmed physical double-tap wake on October30; counter5→6 and
gesture mode0 corroborate the wake. Both services remain active/enabled. Fresh
boot and system-sleep/battery qualification remain pending. Lift-to-wake requires a real motion event backend, which this
Titan build does not expose. Idle policy checks have measured zero service CPU;
this is not a whole-device battery-life measurement.


2026-10-30 input/inventory follow-up: patch `0294-notification-dismissal-policy` restores descendant picking in
the drawer and touch-down gesture eligibility. Clear all and swipes share the
native card's incoming-call protection instead of excluding all critical alerts.
Phone composition now selects Luma Monitor and enforces shared default-app
exclusions for Tour, Help/Yelp, GNOME System Monitor, Reel and Darkroom. Desktop
already excludes these; Creative offerings remain optional Depot installs.
Visible Home pages cache unchanged content, and opening reveal work owns and
cancels its first-paint preparation. Performance is still unqualified: the Titan
has a60Hz reported mode but even a minimal compositor rectangle often paints
around30FPS through its simpledrm/Panfrost path. Do not promise60FPS or fixed
90Hz battery behavior from these changes. No CPU/GPU performance floor remains.


Home Screen preferences (owner approved October31): Settings → Personalization →
Home Screen now controls Mobile and Tablet columns, rows, icon size and app
names. Shared shell-state keys are home-columns, home-rows, home-icon-size and
home-show-labels. Automatic defaults preserve existing layouts; manual counts
are bounded by available touch space. Reset changes only these preferences,
not saved arrangements. Desktop rendering is unchanged.

Matching source cohort: Shell142, Settings60.home20261031.1,
shell-state38.home20261031.1 and Search10. Pure layout regressions, ARM native
settings-owner checks, the actual native Settings build, seven physical Shell
checks and eight physical Settings UI checks passed. The owner accepted the
Titan experience. Other devices require matching package builds; these new
RPM versions have not been built or release-qualified.

The Titan retains a development resource overlay, additive local schema and
native Settings binary bind via project-titan-home-settings.service. Before
installing a proper Settings RPM, close Settings and disable/stop that service
so its bind cannot hide the packaged executable. Keep other overlays and user
layouts intact; deployment and rollback details are in the Titan task record.
