# Pixel 5a (barbet) bring-up findings

Date: 2026-10-21

Status: stock slot `a` boots. Stock images in slot `b` boot with a custom
full-descriptor vbmeta. A mainline kernel has not yet been handed off; the
current blocker is the vendor_boot/DTB layer. No cellular testing has been done.

## Target

- Device: Google Pixel 5a, codename `barbet`
- SKU: `G4S1M` (Japan), product revision `MP1.0(JPN)`
- SoC: Qualcomm SM7250 / Snapdragon 765G
- Current build: `AP2A.240805.005.S4` (Android 14, 2024-08-05)
- Bootloader: `b9-0.6-10898944`, baseband `g7250-00299-240229-B-11514324`
- Bootloader unlocked with `fastboot flashing unlock`; `get_unlock_ability` was
  `1` before unlock. Slot `a` remains stock.

## Recovery assets

Verified Google factory image:

- URL: https://dl.google.com/dl/android/aosp/barbet-ap2a.240805.005.s4-factory-eb064cd6.zip
- SHA-256: `eb064cd61c8e95ace5f2e2a3c2f29d72fb716f2cc4fa12d742c2fae9c934002c`
- Extracted image hashes:
  - `boot.img`: `a0db60a9740ff701c331ff96f28c2358a56f57147482a6283bb0e381af7147f5`
  - `vendor_boot.img`: `14e851027ffcadba26e4e5d268f165eec32e16c96a184b9a4087c866932edc07`
  - `dtbo.img`: `3a7ca01344960a74602b88c7818e63928fce13c1f053141ea743c3e508495ae0`
  - `modem.img`: `f262738a3199b531a092d2d43cd6affd0795460d08aa1a7e86830e066fc6e3d0`
  - `vendor.img`: `e7759089b680a5eb68d7e17af4c6dc7058764b195c9a25ee845eeda1b33e4ee5`
  - `vbmeta.img`: `1b626ea81425ed5f6e9121795f958bc7be637efe68db3e0dc38fd48fc1df7874`
  - `vbmeta_system.img`: `a1b79b2a35b00a542e73f6734e0a2f700f0b112faa8d0536bb3043285c376d87`

Stock boot image: Android boot header v3, 4096-byte pages, 96 MiB partition,
kernel compressed with **standard LZ4 frame** (`04 22 4d 18`), not gzip and not
legacy LZ4 (`02 21 4c 18`). The kernel `Image.lz4` make target produces legacy
LZ4; the standard frame must be produced explicitly.

## AVB findings

`fastboot boot` of a custom image is refused by this bootloader even when
unlocked. The error is:

```
Error verifying the received boot.img: Invalid Parameter
```

This occurred with:

- raw v2 and v3 images
- AVB hash footer with `algorithm NONE`
- a locally generated RSA-4096 key
- the public AOSP AVB test key
- both with and without the `com.android.build.boot.*` AVB properties

A Google-signed stock boot image RAM-boots, so the mechanism works but only for
Google-signed images.

Flashing custom images did not work either until the vbmeta shape was matched.
The key findings:

- A custom vbmeta signed with the public AOSP AVB test key
  (`SHA256_RSA4096`, public key SHA-1 `2597c218aae470a130f61162feaae70afd97f011`)
  is accepted when it has **flags 3** (hashtree and verification disabled)
  and the **full descriptor set** used by the stock image:
  `boot`, `dtbo`, `vendor_boot`, `vendor`, and a `vbmeta_system` chain to
  Google's key (`14e3fd68feb4fe7c9837787ed218d083050574da`).
- With stock `boot`/`vendor_boot`/`dtbo` plus that custom vbmeta, stock Android
  boots in slot `b` (orange unlocked-bootloader warning, then Android).
- A minimal custom vbmeta containing only a `boot` chain and
  `dtbo`/`vendor_boot` hashes is rejected; it returns to fastboot with
  `slot-retry-count:b` still `3`.
- An empty, valid DTBO table with stock boot and stock vendor_boot does not get
  rejected; stock Android boot-loops because the board overlays are missing.
  So an empty DTBO is handed off, not rejected.

## Kernel work

- Base: Linux `v7.3-rc7`, tag commit
  `24a19b953e2a3d0ea5a8c4951a0ab8f32720c907`.
- Applied all 14 patches from `lazybrar/avicii-mainline`, commit
  `780934bbcc758694a9e4e8950eed9c040725df4d`, unmodified. These add SM7250
  clocks, pinctrl, interconnect, UFS, USB, display, GPU, remoteproc and the
  OnePlus Nord board DTS.
- Added `arch/arm64/boot/dts/qcom/sm7250-google-barbet.dts`: minimal UFS, USB
  peripheral, PM8150/PM8150L/PM7250B rails, `simple-framebuffer`, ramoops,
  `uart2`, and an empty-DTBO policy. Display, touch, Wi-Fi, Bluetooth, audio,
  cameras and modem are not enabled yet.
- Config: arm64 defconfig merged with all `avicii-*.config` fragments plus a
  barbet fragment (`DRM_SIMPLEDRM`, framebuffer console, GENI console, EXT4,
  USB configfs NCM/ACM, pstore).
- Built with clang 22 (`LLVM=1`). Kernel image: 48 MiB uncompressed,
  21.8 MiB standard-frame LZ4.
- Initramfs: Alpine 3.24.2 aarch64 + `busybox-extras` 1.37.0, USB NCM/ACM
  gadget, telnet at `172.16.42.1`, watchdog service. This has not run on the
  device yet.
- Avicii patches are kept in the work tree; they are not yet distilled into
  Luma patch files.

## Test matrix

| boot_b | vendor_boot_b | dtbo_b | vbmeta_b | Result |
|---|---|---|---|---|
| stock | stock | stock | stock | Android boots |
| stock | stock | stock | custom full descriptors, flags 3 | Android boots |
| stock | stock | empty valid DTBO | custom full descriptors, flags 3 | Android boot loop (missing overlays) |
| stock | our mainline DTB | stock | custom full descriptors, flags 3 | returns to fastboot (vendor_boot/DTB rejected) |
| our mainline kernel | our mainline DTB | empty | custom full descriptors, flags 3 | returns to fastboot |
| raw custom images | stock/raw | stock | raw/partial custom | returns to fastboot |

The table shows the rejection is in the vendor_boot image/DTB, not the kernel
format and not the full-descriptor vbmeta.

## Next experiments

1. Keep the stock vendor_boot ramdisk but replace only its DTB with the barbet
   DTB; boot with stock kernel and stock DTBO. If this is accepted, the empty
   vendor ramdisk was the problem; if rejected, the DTB content/metadata is.
2. Test our vendor_boot DTB with a forced empty DTBO and stock boot, to separate
   stock DTBO overlay application from DTB validation.
3. If the DTB is rejected, compare root properties against the stock
   vendor_boot DTB (`qcom,msm-id`, `qcom,board-id`, `compatible`, memory node),
   and try a v2 boot image with the DTB embedded so the bootloader uses the
   boot-image DTB instead of the vendor_boot merge path.
4. Once handed off, use the simple-framebuffer console and/or USB gadget to get
   the first kernel log. Then test storage, display, touch, USB, charging,
   Wi-Fi, Bluetooth, audio, modem, camera and suspend one at a time.

## Update: first handoff and early-boot stall

The boot image format was the next blocker. The stock Pixel kernel is a
standard-frame LZ4 image whose `Image` header starts with an ARM64 branch and
has `text_offset=0x80000`. The initial build had an `MZ` EFI PE header and
`text_offset=0`, because `CONFIG_EFI_STUB` was enabled; that image was rejected
even after AVB was solved. Disabling `EFI_STUB`/`EFI` fixed the `MZ` header.

Two more changes were needed to get a real handoff:

- patch `text_offset` in the `Image` header to `0x80000`, and
- move the boot-image ramdisk load address out of the kernel's way
  (`--ramdisk_offset 0x10000000`).

The DTB path was isolated separately:

- our barbet DTB plus the **stock DTBO** is rejected;
- our barbet DTB plus an **empty DTBO** is accepted and handed off (stock
  kernel then boot-loops because Android needs the missing overlays).

With our full kernel, our DTB, an empty DTBO and the stock vendor ramdisk, the
bootloader now hands off. The phone stays on the Google logo, does not bring up
USB or the framebuffer console, and eventually falls back to slot `a`. No kernel
log has been captured yet.

A minimal kernel config was then built to cut the compressed kernel from
21.8 MB to 17.7 MB and remove the display/audio/camera/Wi-Fi/Bluetooth/media
stack. That minimal kernel was tested with the corrected header, the fixed
`/memory` node, our DTB, an empty DTBO and the stock vendor ramdisk. It still
stalls on the Google screen and the bootloader falls back to slot `a`. The
kernel log has not been captured yet, so the early-boot failure is still
unidentified.

Two DTB fixes are in the current build: the `/memory` node is now the stock
no-unit-address shape that the bootloader patches, and the root compatible
string includes `qcom,lito`.

Next:

1. Boot the minimal kernel with the corrected header and relocated ramdisk.
2. If it still stalls, enable USB debugging on stock Android and read
   `/sys/fs/pstore` (or use a debug UART) to capture the early kernel log.
3. If pstore is unavailable, reduce the kernel further and add early debug output
   before attempting display or USB.

## Update 2: size fix, Magisk root, and pstore

The mainline kernel was rebuilt much smaller so its compressed size is below the
stock kernel-to-ramdisk gap:

- compressed kernel: 15,672,350 bytes
- stock gap (`0x1000000 - 0x8000`): 16,744,448 bytes
- boot image uses the stock `--ramdisk_offset 0x1000000` again

That image still stalls on the black Google splash with no USB and no
framebuffer console. The size/overlap theory is therefore not sufficient on its
own.

Separately, slot `b` was rooted as a diagnostic:

- Magisk v27's on-device `boot_patch.sh` patched both the boot and vendor_boot
  ramdisks without the v30.7 "Failed to patch" errors.
- Flashing the patched boot + vendor_boot with a full-descriptor vbmeta
  (flags 3, AOSP test key) boots stock Android from slot `b`.
- `su` works (`u:r:magisk:s0`) and Magisk 30.7 was installed; `magiskd` runs.

Reading pstore is still blocked by SELinux. The `magisk` domain cannot read
`/sys/fs/selinux/policy`, write `use_alt`, mount pstore, or open
`/dev/access-ramoops`; `setenforce 0` and `magiskpolicy --live` are denied. A
Magisk module (`pstorecapture`) with pstore allow rules was installed, but its
`service.sh` did not create `/sdcard/pstore`, so the module path needs more work.

The Android pstore is encrypted and uses the alternate ramoops; the mainline
kernel writes the primary ramoops at `0xB7E41000`. The vendor helper switches
with `/sys/devices/virtual/ramoops/pstore/use_alt`, but access is confined.

Next diagnostic options, in order:

1. Hardware UART on the Pixel 5a debug pads (real early console).
2. Fix the Magisk pstore module: write a marker to `/data/local/tmp`, add
   `shell_data_file`/`cache_file` rules, and verify the service actually runs.
3. Align the barbet DTB reserved-memory and regulators with the Pixel
   downstream map, especially the `qtee_apps_mem`, CDSP/ADSP/venus/camera
   carveouts and UFS rails, then retest.
4. Compare the arm64 Image entry contract and the bootloader's DTB merge
   against the Pixel 9 branch's `inspect-pixel9-kernel-entry.py` approach.

## Update 3: downstream map, console-only kernel, Pixel 5/6

The Pixel-specific carveouts from the downstream vendor_boot DTB were applied:
`qtee_apps_mem` 0x81e00000/0x2600000, camera 0x86000000, NPU 0x86500000,
video 0x86a00000, CVP 0x86f00000 and CDSP 0x87400000. The kernel still stalls on
the black Google splash with no USB and no console.

A console-only kernel was then built:

- `NR_CPUS=8` (instead of 512), no DRM/FB/VT, no NET/SCSI/EXT4, no KALLSYMS,
  no crypto/tracing/perf, USB ACM serial only
- Image: 26,150,344 bytes; LZ4: 9,718,447 bytes
- initramfs: Alpine + ACM gadget, dumps `dmesg` to `/dev/ttyGS0`
- boot image uses the stock `--ramdisk_offset 0x1000000`

It still produced no `/dev/ttyACM0` and no console output. The stall therefore
happens before the USB gadget/initramfs, not at the ramdisk overlap.

Pixel **5 (redfin)** uses the same SM7250/lito platform, but it has no mainline
DTS and its postmarketOS page is also unported. Its downstream redbull DTS is
the same Google source already used here. Pixel **6/9** are Tensor/Exynos; only
the Luma AVB/boot tooling transfers, not the SoC tree.

The primary ramoops was readable as root after the Magisk module (the
`use_alt`/pstore access worked), but it was empty: the kernel does not reach
pstore before stalling. Without an early console (hardware UART) or a known-good
SM7250 reference kernel/DTB, further blind image changes are not converging.

## UART is available

The barbet bootloader accepts both commands:

```
fastboot oem uart enable   -> OKAY
fastboot oem uart disable  -> OKAY
```

The Pixel UART is exposed on the USB-C SBU pins. The working cable pattern from
the public Pixel debug-cable guide is:

- USB-C breakout exposing SBU1/SBU2
- 3.3 V USB-to-TTL serial adapter (FT232/CP2102), 115200 8N1
- GND -> A1, RX -> A8, TX -> B8; do not connect VCC
- `/dev/ttyUSB0` on the host, e.g. `minicom -D /dev/ttyUSB0 -b 115200`

Enable UART with `fastboot oem uart enable` before booting; disable it with
`fastboot oem uart disable` before relocking. UART with an unlocked bootloader
is a debug/root path, so do not leave it enabled for daily use.

With UART, boot the console kernel with `console=ttyMSM0,115200n8` and read the
ABL plus early kernel output. That will show whether the kernel entry runs and
which early clock/regulator/DTB point stops it.

The phone was restored to stock slot `a` and is booting Android normally.

### Host console on this workstation

The host setup uses a **Raspberry Pi Debug Probe** (USB `2e8a:000c`) instead of
the standalone FT232/CP2102 adapter:

- The probe's UART side is CDC-ACM, exposed as `/dev/ttyPICO` (udev symlink;
  otherwise `/dev/ttyACM0`), 115200 8N1, 3.3 V logic, no flow control.
- Wiring: probe `TX` -> phone `RX` (SBU A8), probe `RX` -> phone `TX` (SBU B8),
  `GND` -> `A1`. Do not connect VCC.
- Access comes from `/etc/udev/rules.d/72-rpi-debug-probe.rules` (mode 0666 and
  the `/dev/ttyPICO` symlink), installed by
  `~/Projects/scripts/modules/config_microcontroller.sh`.

`tools/uart-capture.sh` captures a boot log:

```sh
tools/uart-capture.sh                      # 30 s to tmp/uart-<UTC>.log
tools/uart-capture.sh -t 60 -o tmp/boot.log
tools/uart-capture.sh -r                   # start capturing, then fastboot reboot
```

It prints the byte count and log path. Exit status 3 means the port opened but
nothing arrived: check `fastboot oem uart enable`, the SBU wiring, and the baud.
Opening or closing the port does not reset the phone.

Verified 2026-11-11 on serial `18161JECB02915`: 74 KB captured from ABL through
`Linux version 4.19.294-g500b3a2059c1-ab11922802` and into Android userspace on
slot `a`.

## Problems encountered and why there are no kernel logs

There were three separate problem layers.

### Platform

- Pixel 5a (`barbet`, SM7250) has no postmarketOS port and no mainline DTS. The
  postmarketOS page is `port not attempted`.
- The best native-Linux base was the OnePlus Nord SM7250 tree, so the Pixel board
  tree, regulators, carveouts, touch/panel and UFS rails had to be written and
  adapted.
- Pixel 5 uses the same SM7250 platform but also has no mainline DTS/pmOS port.
  Pixel 6/9 are Tensor/Exynos; only the Luma AVB/boot tooling transfers.

### Pixel AVB and boot chain

- `fastboot boot` rejects custom images even with the bootloader unlocked. Only
  Google-signed images RAM-boot.
- Flashed custom images were rejected until the exact Pixel AVB shape was
  matched: AOSP test key, flags 3, and the full descriptor set (`boot`, `dtbo`,
  `vendor_boot`, `vendor`, and a `vbmeta_system` chain).
- The stock DTBO overlays were then applied to the mainline DTB and caused
  rejection; an empty valid DTBO fixed that.
- Further fixes: `/memory` node shape (the bootloader patches `/memory`, not
  `memory@...`), removing the EFI PE header, `text_offset=0x80000`,
  standard-frame LZ4, and a kernel small enough for the boot image layout.

After those fixes the bootloader does hand off to the mainline kernel.

### Why the logs are unavailable

The kernel stalls before every output channel exists:

- **No display console.** The phone stays on the bootloader splash. `simpledrm`
  and fbcon never run; the stock Pixel kernel also has no fbcon.
- **No UART.** The Pixel UART is on the USB-C SBU pins and disabled by default.
  `fastboot oem uart enable` works, but without the debug cable there is no
  serial console.
- **No USB gadget.** The kernel never reaches init, so no NCM/ACM, no
  `/dev/ttyACM0`, and no network/telnet.
- **pstore is empty.** pstore/ramoops is post-mortem RAM storage, not streaming.
  The mainline kernel stalls before pstore registers, so the primary ramoops at
  `0xB7E41000` is empty. Android’s pstore is encrypted, on the alternate
  ramoops, and root-only; Magisk root and a pstore module made the primary
  readable, but there was still no mainline record to read.
- **Android logs are the wrong kernel.** `dmesg`, bugreport and Android ramoops
  describe the stock 4.19 kernel, not the mainline boot.
- **No bootloader/EDL shortcut.** `fastboot fetch` is unsupported, `/dev/mem`
  is unavailable, TWRP has no usable barbet image, and flashing Lineage’s
  vbmeta would raise the rollback index and put stock slot `a` at risk.

Streaming requires a channel that exists before the stall. That is why the next
step is the UART cable: it is the only channel available from power-on, before
any kernel driver initializes.

## Safety boundaries used

- Only slot `b` was written for these tests. Slot `a` remains stock.
- `vbmeta_b` was written only with the user's explicit exception. `vbmeta_a`
  was never written.
- `persist`, `modemst1`, `modemst2`, `fsg`, `fsc` and the bootloader were not
  touched.
- No repartitioning, no relocking.
- The exact factory image is retained for recovery.
