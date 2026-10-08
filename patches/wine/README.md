# Wine downstream patch

Project Luma retains Wine's name, binaries, Windows API behavior, LGPL-2.1-or-
later license, and upstream identity. This patch changes only the presentation
boundary for legacy `Shell_NotifyIcon` information balloons when Wine is
launched by Luma Relay.

`0001-luma-relay-native-notifications.patch` serializes the bounded title/body,
severity, and tray-icon identity, then crosses Wine's supported PE-to-Unix call
boundary through the uniquely named `luma_relay` module. Its Unix side writes
to the per-application socket named by `LUMA_RELAY_NOTIFY_SOCKET`. Relay owns
that socket, validates every message, and publishes the result as the installed
application's native desktop notification. The Windows process never receives
the session D-Bus socket.

If Relay is absent or the private socket cannot be reached, Wine preserves its
upstream balloon behavior. If notifications are explicitly disabled for that
app, the patch suppresses the balloon at the Wine presentation boundary.

The patch deliberately does not scrape windows, inject a global helper, expose
D-Bus, claim support for unimplemented WinRT toast APIs, or change application
window behavior.

Upstream: <https://gitlab.winehq.org/wine/wine>
