# Dock badge evidence harness (Shell patch 0147)

Runs the headless Shell with the badge code (installed or as a resource
overlay) against stand-ins for luma-background, three background agents and a
LauncherEntry sender (`fakes.py`), then captures and checks:

- `MODE=behaviour`: LauncherEntry counts, partial updates, urgent dots, the
  sender quitting; agent precedence, `badge` 0, agents that do not declare
  `badge`; the pop on increase and none on decrease; the
  `dock-badges-disabled-apps` switch; accessible names; Do Not Disturb.
- `MODE=matrix`: counts 1, 9, 12, 99, 128 and a dot on the light, dark, frost
  and glass shelf, with geometry per badge; `measure.py` measures the pill
  height from the screenshots.
- `MODE=edges`: the dock at the bottom, top, left and right.
- `MODE=interact`: hover lift, drag to rearrange and the launch zoom.

`all.sh` runs every mode, the matrix at 100% and 200%. Inside a Fedora
container with the Shell RPMs, mutter, Figtree and python3-pillow installed:
`/oracle/probe.js` is the headless render probe, `/oracle/keyfile` the
GSettings keyfile, and these files live in `/oracle/t`.

Beside the headless Shell runs:

- `flatpak-proxy.sh`: what a Flatpak app may do with
  `com.canonical.Unity.LauncherEntry` through `xdg-dbus-proxy` with the filter
  an app gets without extra D-Bus permissions. Run it inside a session bus:
  `dbus-run-session -- bash flatpak-proxy.sh`.
- `settings-switch.sh` (with `atspi-tree.py`): Settings › Notifications › *app*
  in Xvfb, for the "Badge App Icon" switch. It opens the app's page, records
  the accessibility tree, toggles the switch and reads
  `org.gnome.shell dock-badges-disabled-apps` back each time.
  `settings-switch.sh light|dark`; the app needs
  `X-GNOME-UsesNotifications=true` in its desktop file to be listed at all.

