# Pixel 5a (barbet) support matrix

Bring-up status for the Google Pixel 5a on Luma. `experimental`: it installs,
but functions can regress between revisions and it is not offered to people
who did not go looking for it.

| Area | Status | Evidence and limits |
| --- | --- | --- |
| Boot / full install | built, full-install gate pending | The source lock builds kernel `7.3.0-rc7`, DTB, boot image and the full set; the set is hash-verified. The install writes `userdata` + `boot_b` and preserves the identity/boot-chain partitions. |
| Display | works | Repo-built kernel: `card0-DSI-1` connected at 1080x2400, panel init complete, backlight present. Panel calibration/linearity is not qualified. |
| Touch | works | ST FTS SPI contacts reach the input node, including release; the full-area coordinate calibration is not qualified. |
| Keys | works | Power and volume-down generate input events. |
| GPU | works | `gpu-initialized: 1`; Phoc/Phosh use the render node. |
| Speaker | works | Both TAS2562 amps active; audible through PipeWire once the barbet card is pinned to S16 (24-bit negotiation starts the TDM backend at the wrong slot width and is silent). |
| Microphone | not working | RT5514P DSP firmware loads, but capture returns a constant DC offset with 2-3 LSB of noise; the DMIC/TDM capture route still needs work. |
| Suspend | works (bounded) | `s2idle` enters and wakes by power key; display/touch restore and a post-resume `dwc3` rebind restores USB-NCM. Battery drain is not measured. |
| Wi-Fi | works | The FP6 platform services are masked; `luma-barbet-wifi.service` serves the firmware, stops the ADSP, starts the modem so the WCN3990 boots, then audio restarts the ADSP. `wlan0` associates (`spud_2`), gets DHCP, and resolves DNS/HTTP (200 from fedoraproject.org). The 2.4 GHz link reported 5.5 Mbit/s; throughput is not qualified. |
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
- Wi-Fi waits on the modem/WLFW stack and its memory map; the QRTR channel,
  `tqftpserv`, firmware and board data are already in place.
- The install is full-disk on slot `b`; the factory image is the recovery
  path.
