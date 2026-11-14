# Pixel 5a (barbet) support matrix

Bring-up status for the Google Pixel 5a on Luma. `experimental`: it installs,
but functions can regress between revisions and it is not offered to people
who did not go looking for it.

| Area | Status | Evidence and limits |
| --- | --- | --- |
| Boot / full install | built, full-install gate pending | The source lock builds kernel `7.3.0-rc7`, DTB, boot image and the full set; the set is hash-verified. The install writes `userdata` + `boot_b` and preserves the identity/boot-chain partitions. |
| Display | works | `card0-DSI-1` enabled at 1080x2400; brightness changes are visible. Panel calibration/linearity is not qualified. |
| Touch | works | ST FTS SPI contacts reach the input node, including release; the full-area coordinate calibration is not qualified. |
| Keys | works | Power and volume-down generate input events. |
| GPU | works | `gpu-initialized: 1`; Phoc/Phosh use the render node. |
| Speaker | works | Both TAS2562 amps active (`PWR_CTRL=0x0c`), TDM clock `0x44`; a 2 kHz tone recorded through the mic is ~143x the no-tone baseline. |
| Microphone | works | RT5514P capture: nonzero RMS and ~19.7k distinct values over 5 s. |
| Suspend | works (bounded) | `s2idle` enters and wakes by power key; display/touch restore and a post-resume `dwc3` rebind restores USB-NCM. Battery drain is not measured. |
| Wi-Fi | not working | WCN3990/QRTR kernel support and a start service are present, but the WCN3990 firmware and `tqftpserv` are not in the current firmware set. The earlier manual association is historical evidence, not a works claim. |
| Battery | not working | No `power_supply` device yet; the PM7250B charger is PR 2. |
| Vibrate | not working | No DRV2624 haptics driver yet; PR 2. |
| Storage | works | UFS LUNs and the Luma root filesystem mount; the full install erases `userdata`. |
| Cellular | out of scope | No calls, SMS or mobile data. |
| Cameras, Bluetooth, NFC, fingerprint | out of scope | |

## Limits

- SELinux stays permissive while the port is `experimental`; enforcing is a
  later gate.
- The firmware store is operator-supplied and not redistributable. See
  `INSTALL.md`.
- The WCN3990 board calibration and `tqftpserv` packaging remain the Wi-Fi
  blocker.
- The install is full-disk on slot `b`; the factory image is the recovery
  path.
