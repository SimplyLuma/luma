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
  copied as `bcm2712d0-rpi-5-b.dtb` for D0) with a 256 MB CMA pool, the same
  trees under `dtb/broadcom/` for U-Boot (HDMI audio merged in), the ISO's
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

This installer card boots Fedora's kernel with upstream device trees: no
Bluetooth, HDMI audio, fan, RTC or HEVC. The Luma Pi image (the preinstalled
card, `scripts/devices/build-rpi5-disk-image.sh`) runs `kernel-luma-rpi` on the
firmware's device tree; see `SUPPORT.md` and `src/luma-rpi5-platform/README.md`. 4K at 60 Hz
needs a TV input in HDMI 2.0 (enhanced) mode.

## Hardware results

- 2026-11-01, Pi 5 D0 16 GB, NVMe install: the card menu's `installed` entry
  (one-shot via `next_entry`) ran the installed system's own GRUB menu and
  booted its BLS entry; `next_entry` was cleared afterwards.

This profile is unqualified: an install from a built card image, an update
across a kernel change and a rollback on real hardware must be recorded before
a tier change.

## Test card: Luma Pi kernel (spare microSD only)

A preinstalled card built from a test payload proves the firmware-tree boot
chain before it touches any card in use. It is not for publication (its
manifest says `test_payload`), and `rpm-ostree upgrade` on it would return to
the nightly's Fedora kernel, which does not boot on the firmware tree: do not
upgrade it.

Build (luma-build, as root, under the heavy-job lock):

    P=/srv/dev-storage/projects/luma-pi5
    luma-heavy bash scripts/devices/build-rpi5-disk-image.sh \
      --iso $P/inputs/luma-nightly-<version>-aarch64.iso \
      --uboot-rpm $P/inputs/uboot-images-armv8-2026.10-1.fc45.noarch.rpm \
      --output $P/images/spare-pi-kernel --size 16 \
      --test-payload <ostree repo> <commit with kernel-luma-rpi> \
      --test-ssh-key <public key>

Flash on a Mac (never the Pi's own card):

1. `scp` the `.img.xz` and its `.sha256` from luma-build and check it:
   `shasum -a 256 -c luma-rpi5-*.img.xz.sha256`.
2. Raspberry Pi Imager: Choose OS > Use custom > the `.img.xz`; Choose Storage >
   the spare card; no OS customisation. Or in a terminal:
   `diskutil list` (find the card, e.g. `/dev/disk6`), then
   `diskutil unmountDisk /dev/disk6` and
   `xz -dc luma-rpi5-*.img.xz | sudo dd of=/dev/rdisk6 bs=4m && diskutil eject /dev/disk6`.
3. Power the Pi off, take its card out and keep it safe, insert the spare card,
   power on. The first boot grows the root partition and is slower.

Expected: U-Boot and GRUB pass the firmware's tree (model "Raspberry Pi 5
Model B Rev …", the board's MAC and Bluetooth address), `uname -r` is the Luma
Pi kernel, HDMI audio, Bluetooth and `/dev/video19` (HEVC) are present, and
`luma-rpi5-firmware-sync` (when the payload carries luma-rpi5-platform 0.2.0)
reports the trees in place. To go back, power off and put the original card in.
