# Native Network adapter checks

Run `python3 run-network-extra.py --source /path/to/settings/shell` in the Settings build environment. If the shell is staged separately, pass `--upstream /path/to/full/settings/source` for the existing NetworkManager private test service and QR utility. Requires `gcc`, libnm/JSON/GIO development packages, `dbus-daemon`, Python D-Bus and GI modules.

The test launches its own private bus and NetworkManager test service. It does not modify the machine's network connection.

Passed against the production adapter and fixture on 2026-10-10:

- Explicit native ownership and qualification of hotspot, name, password, band and Ethernet.
- Reject incorrect value types, unsupported band values and Ethernet activation when there is no Ethernet device.
- Add and activate a password-protected hotspot through NetworkManager, then persist its name/password/band with Update2 and verify native saved settings plus model readback.
- Ethernet activation denial emits the in-pane write-error signal and refreshes the actual disconnected state.

The private service does not implement automatic wired profile selection. Successful physical Ethernet activation, hotspot radio coverage and DHCP reachability are not asserted by this test.

The existing `test-luma-live-network` target continues to cover native join/forget UI, repeated refresh and cancellation behavior.
