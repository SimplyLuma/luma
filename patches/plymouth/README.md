# Plymouth downstream

Project Luma carries four narrow patches, and a defaults change, over Fedora 44's exact
`plymouth-24.004.60-24.fc44` source package.

- `0001-prairie-light-details.patch` changes the native details plugin's
  terminal foreground/background selection to black on white. The plugin
  continues to render Plymouth's real buffered boot stream and retains its
  existing Escape toggle, password prompts, and logging behavior.
- `0002-script-firmware-background.patch` gives the script plugin the firmware
  boot logo (ACPI BGRT) that two-step's `UseFirmwareBackground` already draws:
  `Window.HasFirmwareBackground()`, `Window.SetFirmwareBackgroundOpacity(v)` and
  `Window.GetFirmwareBackgroundOpacity()`. The logo is placed with two-step's
  own rules, so a script theme can start on exactly the screen the firmware
  left and fade to its own. The firmware's screen is composed in device
  pixels (the raw logo at its raw offsets) and rebuilt per canvas size and
  device scale, so HiDPI displays match the firmware exactly. The opacity defaults to 0, so existing script
  themes are unchanged. It also lets `Plymouth.GetMode()` follow
  `plymouth change-mode`, which the script plugin otherwise never saw.

- `0003-script-get-time.patch` adds `Plymouth.GetTime()`, the monotonic clock
  in seconds, so a script theme can time animation by the clock rather than by
  counting refresh callbacks, which arrive late when frames are slow.
- `0004-device-manager-retry-drm-outputs.patch` retries a DRM device whose
  outputs cannot be driven yet ("Could not initialize heads") every 100 ms for
  up to 3 s. Without it, when a GPU driver replaces SimpleDRM before its
  connector reports a mode, Plymouth has no display until DeviceTimeout and
  the screen stays frozen on the firmware's image.
- The spec patch sets `Theme=luma-loading` in `plymouthd.defaults`: Luma's
  splash is selected in `/usr`, and `/etc/plymouth/plymouthd.conf` stays the
  administrator's file (ADR-045). `%check` verifies the defaults and the
  plugin APIs.

- Upstream: <https://gitlab.freedesktop.org/plymouth/plymouth>
- Fedora package: `plymouth-24.004.60-24.fc44`
- License: GPL-2.0-or-later
- Luma patch: GPL-2.0-or-later
