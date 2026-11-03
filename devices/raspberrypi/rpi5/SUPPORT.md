# Raspberry Pi support matrix

What the established Raspberry Pi images ship for the Pi 5 family, what the
Luma Pi image ships, and the state of each item. Surveyed 2026-11-02 against
Raspberry Pi OS (Bookworm/Trixie), Ubuntu 24.04/25.x for Raspberry Pi,
Fedora 44 aarch64 (Workstation/IoT), Manjaro ARM and LibreELEC 12/13.

Status: **done** = in this branch and checked on a Pi 5; **built** = in this
branch, not yet checked on hardware; **todo** = not in Luma yet;
**decision** = waiting on Nick.

## Kernel and boot

| Item | RPi OS | Ubuntu | Fedora | Manjaro ARM | LibreELEC | Luma | Status |
|---|---|---|---|---|---|---|---|
| Kernel tree | raspberrypi/linux LTS (kernel8 4K, kernel_2712 16K) | linux-raspi (Raspberry Pi tree on Ubuntu's) | mainline | linux-rpi4/-rpi5 (Raspberry Pi tree) | Raspberry Pi tree + LE patches | `kernel-luma-rpi`: raspberrypi/linux rpi-7.2.y, Fedora 7.2 config + bcm2711_defconfig, 4K pages | built |
| Boards | Pi 0 2 W-5, 400, 500, CM3-CM5 | Pi 3/4/400/5/500, CM4/CM5 | Pi 3/4/5 (partial on 5) | Pi 3/4/400/5 | Pi 2-5 | kernel: Pi 3, 4, 400, 5, 500, CM4, CM5; card: Pi 5 only (`[pi5]`) | built |
| Device tree | firmware DT + `overlays/` + `config.txt` | same | U-Boot loads the kernel's upstream DTB | same as RPi OS | same as RPi OS | Raspberry Pi DTBs + 400+ overlays in `/usr/lib/modules/<kver>/dtb/{broadcom,overlays}`; card copies to its FAT root | built; card wiring todo |
| Boot chain | firmware → kernel | firmware → kernel (tryboot A/B for kernel updates) | firmware → U-Boot → GRUB | firmware → kernel | firmware → kernel | firmware → U-Boot → GRUB → ostree BLS (rollback, greenboot) | done (Fedora kernel) |
| Page size | 16K on Pi 5 | 4K | 4K | 16K (rpi5) | 16K/4K | 4K: Android apps, Waydroid, box64/FEX assume 4K | built |
| Config parity | — | — | — | — | — | `merge-rpi-config.py check`: every Fedora and Raspberry Pi option kept or listed in `config/kernel/rpi-config-exceptions.txt` (SELinux, BPF LSM + BTF, Rust, btrfs, erofs/composefs, overlayfs, fs-verity, dm-crypt/verity, TPM, nftables, WireGuard, binder, ntsync, cgroups v2) | built |

## Hardware

| Item | RPi OS | Ubuntu | Fedora | Manjaro ARM | LibreELEC | Luma | Status |
|---|---|---|---|---|---|---|---|
| Wi-Fi (CYW43455) | brcmfmac, RPi firmware-nonfree | linux-firmware | linux-firmware (brcmfmac-firmware) | firmware-raspberrypi | yes | Fedora brcmfmac-firmware + NVRAM alias (luma-rpi5-platform) | done |
| Wi-Fi regulatory domain | raspi-config / `cfg80211.ieee80211_regdom` | wireless-regdb | wireless-regdb | wireless-regdb | yes | wireless-regdb; country from NetworkManager | todo: check country is set |
| Bluetooth (CYW43455, BT UART) | bluez-firmware (RPi), serdev hci_uart | linux-firmware-raspi, pi-bluetooth | no (no 8250_bcm7271, no .hcd) | firmware-raspberrypi | yes | `8250_bcm7271`, `hci_uart` BCM; `luma-rpi-bluetooth-firmware` (BCM4345C0/C5, 43430A1/B0, 4343A2) | done: patch RAM build 0382, scan finds devices (2026-11-02) |
| HDMI video, 2 × 4Kp60 | vc4 KMS | vc4 | vc4 | vc4 | vc4 | vc4 (Raspberry Pi tree) | built |
| HDMI audio, both ports | yes | yes | no on Pi 5 (no BCM2712 DMA) | yes | yes | Raspberry Pi tree has the BCM2712 DMA engine; retires `luma-rpi5-kmod` | done: two vc4hdmi cards, playback through PipeWire on HDMI 0 |
| HDMI-CEC | vc4 CEC, cec-utils, libcec | yes | vc4 CEC | yes | Kodi (libcec) | `DRM_VC4_HDMI_CEC`; Fedora v4l-utils (`cec-ctl`), libcec; card sets `hdmi_ignore_cec_init=1` | built |
| GPU GL/Vulkan (V3D 7.1) | Mesa v3d/v3dv | Mesa | Mesa | Mesa | Mesa | Fedora Mesa; GTK GL renderer, V3D autosuspend (luma-rpi5-platform) | done |
| HEVC decode (up to 4Kp60) | rpi-hevc-dec + FFmpeg fork (v4l2-request, SAND) + VLC/mpv/Chromium | rpi-hevc-dec | none | yes | rpi-hevc-dec + FFmpeg v4l2-request + Kodi | `rpi-hevc-dec` + GStreamer `v4l2slh265dec` with one Luma patch (`gstreamer1-plugins-bad-free` `.luma`); FFmpeg path see below | done: 2160p60 decodes at 62.6 fps, plays zero-copy to the compositor at 6 % CPU |
| H.264 decode | software (Pi 5 has no H.264 block) | software | software | software | software | software (openh264) | n/a |
| Video encode | software (no block on Pi 5) | software | software | software | software | software | n/a |
| Cameras (CSI, PiSP) | rp1-cfe, pisp-be, libcamera fork, rpicam-apps | libcamera, rpicam-apps | libcamera 0.7.2 with rpi/vc4 only (Pi 4), no PiSP | libcamera, rpicam-apps | no | kernel: rp1-cfe, pisp-be, imx219/477/500/708 and the rest; userspace needs libpisp + libcamera rpi/pisp + tuning files | kernel built; userspace todo |
| RTC + battery charging | rtc-rpi, `dtparam=rtc_bbat_vchg` | yes | no | yes | yes | `rtc-rpi` built in; dtparam in `config.txt` | built |
| Fan (Pi 5 Active Cooler, case fan) | pwm-fan + cooling maps (firmware DT) | yes | no | yes | yes | `pwm-fan`, `PWM_RP1`, firmware DT cooling maps | built |
| Argon ONE V3 case | Argon40 `argononed` (I2C MCU fan, power button) | same script | no | same script | add-on | kernel i2c-dev; userspace daemon not packaged | todo |
| PoE+ HAT | rpi-poe-power + PWM fan via firmware, HAT EEPROM overlay | yes | no | yes | yes | `PWM_RASPBERRYPI_POE`, `RPI_POE_POWER`; overlay from HAT EEPROM | built |
| GPIO (RP1 gpiochip) | libgpiod, pinctrl, gpiozero/lgpio | libgpiod, lgpio | libgpiod | libgpiod | — | `PINCTRL_RP1`; Fedora libgpiod-utils, python3-libgpiod, python3-gpiozero; lgpio, pinctrl not in Fedora | built; lgpio/pinctrl todo |
| I2C / SPI / UART | `dtparam=i2c_arm=on,spi=on`, `enable_uart` | yes | partial | yes | yes | DesignWare I2C/SPI, i2c-dev, spidev; Fedora i2c-tools, spi-tools | built |
| PCIe / NVMe, Gen 3 option | `dtparam=pciex1_gen=3` | yes | yes (Gen 2) | yes | yes | `pcie-brcmstb`; dtparam via firmware DT | done (Gen 2); Gen 3 built |
| USB / NVMe boot order | EEPROM `BOOT_ORDER` | yes | — | yes | yes | card boot; EEPROM tool todo | todo |
| EEPROM updates | rpi-eeprom + rpi-eeprom-update service | rpi-eeprom | not packaged | rpi-eeprom | yes | not packaged | todo |
| vcgencmd, pinctrl, dtoverlay, raspinfo | raspi-utils | raspi-utils | not packaged | raspberrypi-utils | partial | not packaged | todo |
| Watchdog | bcm2835-wdt | yes | yes | yes | yes | `bcm2835_wdt` built in | built |
| Thermal, cpufreq | raspberrypi-cpufreq, ondemand | schedutil | scmi/raspberrypi-cpufreq | ondemand | ondemand | raspberrypi-cpufreq, schedutil (Fedora) | built |
| Power button | gpio-keys `KEY_POWER` in DT → logind | yes | no on Pi 5 | yes | yes | firmware DT + systemd-logind | built |
| Sound HATs (DAC overlays) | snd-soc HAT drivers + overlays | yes | some | yes | yes | all bcm2711_defconfig HAT drivers (HiFiBerry, IQaudIO, JustBoom, Allo...) + overlays | built |
| DSI/DPI displays, touch, composite | vc4-kms-dsi-*, ili9881c, goodix, RP1 DSI/DPI/VEC | yes | partial | yes | yes | `DRM_RP1_DSI/DPI/VEC`, panels, touch from defconfig + overlays | built |
| CMA / memory split | `dtoverlay=vc4-kms-v3d-pi5,cma-*` | yes | `cma=` | yes | yes | 256 MB below 1 GB (card DT today; `cma-256` overlay parameter with firmware DT) | done; overlay todo |
| Hailo AI HAT | hailo-all (out-of-tree hailo_pci) | — | — | — | — | out-of-tree driver, not packaged | todo |
| Raspberry Pi Imager listing | yes | yes | — | yes | yes | custom repository JSON possible | todo |

## HEVC userspace and patents

HEVC is covered by patent pools; Fedora ships no HEVC software decoder. What
each piece is and where it stands in Fedora 44 (facts, not legal advice):

| Piece | Role | In Fedora 44 |
|---|---|---|
| `rpi-hevc-dec` kernel driver | drives the Pi's hardware block | not in mainline (Raspberry Pi tree only); Luma builds it in `kernel-luma-rpi` |
| GStreamer `h265parse` (videoparsersbad) | splits the bitstream into NAL units | shipped in gstreamer1-plugins-bad-free 1.28.8 |
| GStreamer `v4l2slh265dec` (v4l2codecs) | parses slice headers, drives V4L2 stateless decoders | shipped in gstreamer1-plugins-bad-free 1.28.8; maps the Pi's `NC12` output to `NV12` + `DRM_FORMAT_MOD_BROADCOM_SAND128` (8-bit only; 10-bit `NC30` has no mapping) |
| FFmpeg `hevc` decoder | software decoder and the parser FFmpeg's hwaccels use | disabled in ffmpeg-free (`--disable-decoder=h264,hevc,vc1,vvc`) |
| FFmpeg `v4l2-request` hwaccel | the path Raspberry Pi OS, LibreELEC, mpv and VLC use | not in upstream FFmpeg 8.1; Raspberry Pi and LibreELEC patch it in |

So hardware HEVC through GStreamer (`playbin` in Tide/GTK apps, `gtk4paintablesink`,
`waylandsink`) needs nothing Fedora excludes. Fedora's 1.28.8 cannot negotiate
the decoder's SAND128 output with any sink; Luma's `gstreamer1-plugins-bad-free`
carries a one-function fix (`patches/gstreamer1-plugins-bad`). Mesa 26.2 imports
NV12/P030 SAND128 as external textures, and vc4 planes scan it out directly. Players built on FFmpeg (mpv, VLC, Celluloid,
Kodi) and FFmpeg itself would need a Luma FFmpeg with the HEVC decoder enabled
plus the v4l2-request patches: that is the part Fedora excludes, and it is
Nick's decision. Viola (Chromium) decodes through VA-API on Linux; Chromium's
V4L2 stateless decoder is a ChromeOS build option, so Viola has no Pi HEVC
hardware path either way.

## Firmware and licences

| Blob | Source | Licence | Redistribution |
|---|---|---|---|
| CYW43455 Wi-Fi (`cyfmac43455-sdio.*`, `brcmfmac43455-sdio.*`) | Fedora brcmfmac-firmware (linux-firmware) | LICENCE.cypress | yes, already in Luma's base |
| Bluetooth `.hcd` (BCM4345C0/C5, BCM43430A1/B0, BCM4343A2) | RPi-Distro/bluez-firmware `pios/trixie` cdf61dc | LICENCE.cypress (`LICENSES/LicenseRef-Cypress-Wireless-Firmware.txt`) | yes: object-code redistribution for use with Cypress chips; Raspberry Pi states the files are released for redistribution under that licence (Phil Elwell, 2021-09-06, in the repo's debian/copyright); Debian and Ubuntu ship them |
| Board firmware, bootloader | Pi 5 EEPROM (on the board) | Raspberry Pi | not shipped by Luma |
| U-Boot | Fedora uboot-images-armv8 | GPL-2.0-or-later | yes |

## Boot chain with the Luma Pi kernel

Decision (2026-11-02): option A. The board firmware builds the tree from the
Raspberry Pi `.dtb` files, `overlays/` and `config.txt` on the card; U-Boot and
GRUB pass it on; `luma-rpi5-firmware-sync` refreshes the files after a good
boot. Fedora-kernel boots (installer card, Fedora-kernel deployments) keep the
U-Boot layout with upstream trees. Implemented in
`scripts/devices/rpi5-board-dtbs.sh`, `build-rpi5-disk-image.sh` (layout chosen
from the payload's kernel) and luma-rpi5-platform; proved on a spare card
before any change to an existing card.

Proved on a Pi 5 D0 (2026-11-02): an rpm-ostree deployment with
`kernel-luma-rpi`, `luma-rpi-bluetooth-firmware` and the `.luma`
`gstreamer1-plugins-bad-free` boots cleanly (no failed units, no SELinux
denials, HDMI audio, Bluetooth, HEVC) when the kernel gets Raspberry Pi's tree
with the board's fixups. What the firmware adds: MAC (`local-mac-address`),
Bluetooth address (`local-bd-address`), serial and model, memory, `pciex1`
(NVMe) and every `dtoverlay=`/`dtparam=` in `config.txt`.

Options considered:

| Option | How | For | Against |
|---|---|---|---|
| A. Firmware tree through U-Boot | FAT root holds `kernel-luma-rpi`'s `dtb/broadcom/*.dtb` and `overlays/`; `config.txt` carries `dtoverlay=vc4-kms-v3d-pi5,cma-256`, `dtparam=pciex1` and user lines; no `dtb/` directory, so U-Boot passes the firmware's tree to GRUB and Linux. A luma-rpi5-platform service copies the booted kernel's trees to the FAT after a good boot. | What Raspberry Pi OS, Ubuntu, Manjaro and LibreELEC do: all fixups, per board (C1/D0/500/CM5), HATs and overlays work. | Fedora's kernel hangs on the firmware tree (twice on 2026-11-02, including the following normal boot; a card trip to undo), so the installer, rescue and any Fedora-kernel rollback need a GRUB `devicetree` entry with the upstream tree. U-Boot on the downstream tree is not yet proved on this card. Trees on the FAT are shared by all deployments. |
| B. U-Boot loads Raspberry Pi trees from `dtb/broadcom/` | Card build merges `vc4-kms-v3d-pi5`, `pciex1` and CMA into the Raspberry Pi trees with `fdtoverlay` (as it does for HDMI audio today) under U-Boot's names. | Same chain as today; U-Boot adds memory and MAC. | No Bluetooth address (controller unconfigured), no `config.txt` overlays or HATs; Fedora kernel paths break the same way as A. |
| C. BLS `devicetree` per deployment | Kernel package ships `/usr/lib/modules/<kver>/devicetree`; ostree writes a `devicetree` line. | Rollback to a Fedora-kernel deployment keeps its own tree. | One board per package; GRUB applies no fixups (random MAC, no Bluetooth address, no overlays). Test use only. |
| D. Firmware boots Linux directly | Kernel, initramfs and `cmdline.txt` on the FAT, refreshed from the default deployment; `tryboot` for A/B. | No U-Boot or GRUB. | Loses GRUB/BLS menu and greenboot counting; new update tooling. |

Open: the installer card cannot pick a C1 or D0 upstream tree for a GRUB
`devicetree` line by itself (GRUB cannot see the board revision), so it keeps
the U-Boot layout and suits Fedora-kernel payloads only; a Luma Pi kernel
installer needs its own design.

## Nick's Pi: transitional state (2026-11-02)

- Deployments: index 0 (default) Fedora kernel 7.2.11 on U-Boot's upstream
  trees; index 1 (rollback slot) `kernel-luma-rpi` 7.2.15 +
  `luma-rpi-bluetooth-firmware` + `.luma` GStreamer (commit afe2fb32).
- The Luma Pi kernel boots only through the card's one-shot entry
  `luma-rpi-deploy` (`grub2-editenv /run/media/nick/LUMA_PI5/EFI/BOOT/grubenv
  set next_entry=luma-rpi-deploy`, then reboot), which loads
  `luma-rpi-kernel/bcm2712d0-rpi-5-b.dtb` (Raspberry Pi D0 tree with this
  board's MAC, Bluetooth address, serial/model, `pciex1` and 256 MiB CMA
  baked in) with GRUB `devicetree`. Test only; the card's default path is
  unchanged.
- Risk 1: `rpm-ostree rollback` (or `ostree admin set-default 1`) makes the
  rollback slot the default; its BLS entry has no `devicetree`, so it would
  boot Raspberry Pi's kernel on U-Boot's upstream tree (untested; may hang).
  Do not run either until the card is converted to option A.
- Risk 2: the installed GRUB's grubenv has `fallback=1`: if the default
  entry fails to load in GRUB (missing kernel or initramfs), GRUB boots
  entry 1, the same untested combination. A power cycle with
  `next_entry=installed-debug` on the card recovers.
- greenboot cannot roll back on its own today: there is no `boot_counter` in
  `/boot/grub2/grubenv` (it is set only when a new deployment is staged).
- Removing the transition: once the card is converted to option A, make the
  Pi kernel deployment the default and drop the `luma-rpi-kernel/` folder
  and the `luma-rpi-kernel` / `luma-rpi-deploy` entries from the card's
  `EFI/BOOT/grub.cfg` (backup: `grub.cfg.before-luma-rpi-kernel`).
