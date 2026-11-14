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
| Microphone | works | The RT5514P capture only produces usable samples with a 32 kHz front end: the machine driver pins the capture front end to 32 kHz while the primary TDM stays at 48 kHz, and the ADSP converts between them. A 1 kHz speaker tone is captured at 1000 Hz with 16k+ distinct samples. The card is registered after the ADSP boots, so a session unit starts PipeWire/WirePlumber and a rescan service sends the udev change event WirePlumber needs on a cold boot. |
| Suspend | works (bounded) | `s2idle` enters and wakes by power key; display/touch restore and a post-resume `dwc3` rebind restores USB-NCM. Battery drain is not measured. |
| Wi-Fi | works | The WLAN chip must boot before the ADSP: `luma-barbet-wifi.service` runs before audio, serves the firmware, starts the modem, and waits for `wlan0`; audio then starts the ADSP. `wlan0` and `WLFW` appear on the freshly installed set. |
| Battery | works (approximate) | `battery` supply from the PM7250B VADC `vbat_sns`: voltage and a linear 3.2-4.35 V capacity; status comes from `pm7250b-usb`. Not coulomb-counted; the Qualcomm QG driver has no mainline equivalent. |
| Vibrate | driver ready, feel test pending | DRV2624 RTP driver binds at I2C `0x5a` and registers `drv2624:haptics` with FF rumble. Manual vibration check still needed. |
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
