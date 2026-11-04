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
firmware's device tree, as an override of the generic ARM commit; see `SUPPORT.md` and `src/luma-rpi5-platform/README.md`. 4K at 60 Hz
needs a TV input in HDMI 2.0 (enhanced) mode.

## Hardware results

- 2026-11-01, Pi 5 D0 16 GB, NVMe install: the card menu's `installed` entry
  (one-shot via `next_entry`) ran the installed system's own GRUB menu and
  booted its BLS entry; `next_entry` was cleared afterwards.

This profile is unqualified: an install from a built card image, an update
across a kernel change and a rollback on real hardware must be recorded before
a tier change.

## Preinstalled card: one ARM OS, Pi kernel override

The preinstalled card (`scripts/devices/build-rpi5-disk-image.sh`) carries the
same signed OS commit as every other ARM computer (`luma/1/aarch64/<channel>`)
and runs `kernel-luma-rpi` as a client-side rpm-ostree override (Fedora's
kernel packages removed), applied when the card is built, so the first boot
needs no network. There is no Raspberry Pi OS ref, graph or Depot entry. The
card keeps `/etc/yum.repos.d/luma-raspberrypi-rpi5.repo` (the signed Raspberry
Pi 5 device repository, only `kernel-luma-rpi`, signed with the Luma OS
release key) and that key armored for rpm,
`/etc/pki/rpm-gpg/RPM-GPG-KEY-luma-os-release`; every update
(luma-update or `rpm-ostree upgrade`) rebuilds the override on the new commit
and takes newer Pi kernels from that repository. `rpm-ostree status` shows the
override.

The first boot imports the offline apps and needs several GB free after the
root grows: the 14 GiB image (the override adds a second tree while the card is built) needs a card (or a VM disk) of 16 GB or more.

Inputs: the night's aarch64 ISO (release key, update URL), the signed OSTree
repository holding the generic commit, and the signed device repository
(rpm-md with `repomd.xml.asc`, RPMs signed with the Luma OS release key) and
that key, armored (`RPM-GPG-KEY-luma-os-release` beside the repository).

1. Gate card (luma-build, as root, unconfined: a system service may not write
   SELinux types the host's policy lacks; about 25 minutes, 10 of them the
   override's initramfs under qemu-user):

       systemd-run --scope luma-heavy bash scripts/devices/build-rpi5-disk-image.sh \
         --iso <aarch64 ISO> --output <dir>/gate --no-xz \
         --test-payload <signed repo> <commit> --test-ssh-key <key.pub> \
         --device-repo <device repo> --device-key <key.asc> \
         [--local-package <new package>.rpm]... [--replace-local <base package>.rpm]...

2. Gate (an aarch64 host with qemu; the Mac uses HVF): boots the card, checks
   the generic commit and origin, the override, greenboot, the image contract
   and first-boot setup, then runs an rpm-ostree upgrade that must reapply the
   override and boot the Pi kernel again:

       scripts/devices/gate-rpi5-card.sh --image <gate card>.img --key <key> \
         --manifest <gate card>.manifest.txt --device-repo <device repo> \
         --expect-commit <commit> --output gate-result.json

3. Release card, from the same inputs with the signed ref instead of the test
   payload: `--release --payload <signed repo> luma/1/aarch64/<channel>`.
   `--release` requires the override, refuses test payloads and keys, and
   checks that the card gives no root SSH access.

`--local-package` and `--replace-local` layer RPM files the commit does not
carry yet (the Pi platform, Bluetooth firmware, GStreamer with the HEVC
fix); once a nightly ships them, build without them. Every card refuses a
hardware-targeted commit and an `/etc/luma/update.conf` that names a hardware
target (luma-rpi5-platform before 0.4.0).

Flash on a Mac (never a card in use):

1. `scp` the `.img.xz` and its `.sha256` from luma-build and check it:
   `shasum -a 256 -c luma-rpi5-*.img.xz.sha256`.
2. Raspberry Pi Imager: Choose OS > Use custom > the `.img.xz`; Choose Storage >
   the card; no OS customisation. Or in a terminal:
   `diskutil list` (find the card, e.g. `/dev/disk6`), then
   `diskutil unmountDisk /dev/disk6` and
   `xz -dc luma-rpi5-*.img.xz | sudo dd of=/dev/rdisk6 bs=4m && diskutil eject /dev/disk6`.
3. Insert the card and power on. The first boot grows the root partition,
   imports the apps and opens Luma's setup.

Expected: U-Boot and GRUB pass the firmware's tree (model "Raspberry Pi 5
Model B Rev …", the board's MAC and Bluetooth address), `uname -r` is the Luma
Pi kernel, HDMI audio, Bluetooth and `/dev/video19` (HEVC) are present, and
`luma-rpi5-firmware-sync` reports the trees in place.
