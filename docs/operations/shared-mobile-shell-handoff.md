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

## Source to carry over

| Area | Maintained source |
| --- | --- |
| Home, dock, paging, widgets, shared search, gestures and app motion | `src/luma-shell-presentation/` |
| Compact login, automatic hardware-keyboard/OSK behavior | `patches/gnome-shell/0290-compact-login-action-bar.patch`, `0291-shared-handheld-startup-and-input.patch` |
| Notification island, full-screen Mobile tray, lock privacy and touch target | `patches/gnome-shell/0292-display-exclusions-and-notification-touch.patch`, `0293-mobile-lock-notification-island.patch` |
| Shared window geometry, safe areas and app action bars | `src/luma-platform/appkit/luma_appkit/`, `src/luma-platform/ui/` and maintained app/Mutter patches |
| Live Weather/Calendar models and background refresh | `src/luma-home-widgets/`, `src/prairie-core/prairie_apps/weather*.py` |
| Maintained native Terminal and shared command action bar | `src/luma-terminal/` |
| Mode/state service | `src/luma-shell-state/` |
| Device display exclusions | `config/mobile/display-geometry/` |
| Input, power and appearance defaults | `config/mobile/luma-shell-handheld.dconf` |

The source candidate pins are Shell `1.luma.125.mobile20261030.1`, developer
platform `1.luma.116.mobile20261030.1`, core apps
`1.luma.124.mobile20261030.2` and widgets `1.luma.3`. These identify requested
builds, not published or qualified RPMs. Preserve newer work already present in
the receiving branch/device; the Titan's private development install includes
newer unrelated platform UI work, so do not downgrade an installed package tuple
just to match an older base.

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
Desktop/Tablet lock presentation remains separate.

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

Native Tide/Connect source synchronization repairs and the matching Tide build
are documented in [Tide server sync](tide-connect-sync.md).
