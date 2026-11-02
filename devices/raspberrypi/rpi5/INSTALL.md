# Raspberry Pi 5

This experimental profile is for the Raspberry Pi 5 Model B, C1 and D0
steppings, all memory sizes. It does not cover the Pi 500 or Compute Module 5.
Luma installs to an NVMe drive on the PCIe connector or to a USB drive; the SD
card stays in the Pi as its boot card.

## The card image

`scripts/devices/build-rpi5-sd-image.sh` turns a nightly aarch64 installer ISO
into the card image:

- Partition 1, FAT32 `LUMA_PI5`: `config.txt`, Fedora's U-Boot
  (`uboot-images-armv8`, `rpi_arm64/u-boot.bin`), the installer kernel's
  upstream device trees (`bcm2712-rpi-5-b.dtb` for C1, `bcm2712-d-rpi-5-b.dtb`
  copied as `bcm2712d0-rpi-5-b.dtb` for D0) with a 256 MB CMA pool, the ISO's
  GRUB as `EFI/BOOT/BOOTAA64.EFI`, the card menu, and the installer kernel.
- Partition 2: the ISO, byte for byte.

The installer on a card can be older than the current release. Install, then
update; the installed system updates itself and the card is not rewritten.

## Installing

1. Write the image to a microSD card of at least 16 GB with Raspberry Pi
   Imager ("Use custom") or `dd`. Writing replaces everything on the card.
2. Connect HDMI (the port next to USB-C), a keyboard and mouse, and Ethernet
   or plan to join Wi-Fi in the installer. Power on.
3. The card menu starts the installer. Choose the NVMe or USB drive as the
   installation disk. Never choose the SD card itself.
4. After installing, leave the card in. Its menu now starts the installed
   Luma by default, through that system's own boot menu, so kernel updates and
   rollbacks need no change on the card.

The board must boot from the SD card first (the default boot order). The GRUB
menu cannot be driven with a USB keyboard; from a running Luma,
`sudo luma-rpi5-boot-next install` (or `rescue`) chooses the next boot once.

Do not use the Raspberry Pi OS device trees: the display works with them, but
USB and Ethernet (RP1) stop working under the Fedora kernel. Do not add a
`console=` argument to the installer: Anaconda then starts in text mode on a
serial console. The installer leaves EFI boot entries alone on this board,
because U-Boot stores them on the card and an entry would bypass the card menu.

## Known limits

Bluetooth, fan control, the RTC and the power button do not work on the
mainline kernel yet; HDMI audio needs `luma-rpi5-kmod` and the card overlay.
See `src/luma-rpi5-platform/README.md`. 4K at 60 Hz
needs a TV input in HDMI 2.0 (enhanced) mode.

## Hardware results

- 2026-11-01, Pi 5 D0 16 GB, NVMe install: the card menu's `installed` entry
  (one-shot via `next_entry`) ran the installed system's own GRUB menu and
  booted its BLS entry; `next_entry` was cleared afterwards.

This profile is unqualified: an install from a built card image, an update
across a kernel change and a rollback on real hardware must be recorded before
a tier change.
