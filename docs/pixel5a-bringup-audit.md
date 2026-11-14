# Pixel 5a bring-up audit, 2026-10-21

## Native boot demonstrated, 2026-11-11 — current handoff

**Current device state:** stock Android has fully booted on slot `a`, confirmed
by ADB (`sys.boot_completed=1`, `_a`, build `AP2A.240805.005.S4`). The native
diagnostic was flashed to `boot_b` and its normal partition boot was verified
over UART. Its deliberate 90-second reboot timer ran repeatedly without marking
the slot successful; ABL exhausted the retry count, marked `b` unbootable and
fell back to stock `a`. This is an observed diagnostic retry/fallback sequence,
not evidence that native init failed. Reactivate `b` explicitly in fastboot for
another test; do not expect it to remain selected or be daily-drivable.

The user explicitly authorized continuing to a booting native kernel and
slot-`b` flashing after the RAM-only investigation. This authorizes scoped
slot-`b` testing, not bootloader/modem/persist writes or rollback-index changes.
Slot `b` is not inherently risk-free. The older gates below describe the audit
as it stood before that authorization and these new observations.

Before any test, a rooted factory-kernel RAM control enabled full backups of
`boot_b` (96 MiB), `vendor_boot_b` (96 MiB), `dtbo_b` (16 MiB) and `vbmeta_b`
(64 KiB). These are retained privately at
`~/luma-barbet-work/uart-review-20261111/slot-b-before-native/`, with sizes and
SHA-256 in `manifest.json`, checked against the phone's own SHA-256 output.
They supersede the zero-byte non-backups discussed in the historical audit.
The rooted Magisk control can run existing modules and write userdata; it is
not claimed to be a zero-write Android boot.

Fresh fastboot queries confirmed slot `a` successful/not-unbootable and slot
`b` not-successful/not-unbootable, with two retries. The existing `dtbo_b` was
already a valid zero-entry table. Existing `vbmeta_b` was already the retained
full-descriptor, AOSP-test-key, flags-3 console vbmeta, rollback index
1722816000. Neither partition needed rewriting. Slot `a` boot/vendor_boot/dtbo
hashes matched the factory images; the actual `vbmeta_a` baseline was recorded
separately (it differs from the factory file). No rollback metadata was changed.

After `fastboot -s 18161JECB02915 set_active b`, a v2 RAM image used the audited
interconnect-enabled kernel, the corrected board DTB, and a minimal Alpine
BusyBox/musl initramfs that mounts no persistent storage. Its command line was:

```text
earlycon=qcom_geni,0x888000 keep_bootcon console=ttyMSM0,115200n8 ignore_loglevel loglevel=8 panic=15 rdinit=/init
```

Header fields used 4096-byte pages, base 0, kernel offset `0x8000`, ramdisk
offset `0x10000000`, DTB offset `0x18000000`, tags offset `0x100`, version 2,
Android 14.0.0 and patch month 2024-08. These are packaging values, not observed
runtime addresses. Standard-frame LZ4 was used for the kernel; gzip for the
standalone v2 ramdisk.

The first image was rejected before entry. UART established the exact reason:

```text
Not valid dtbo found, use only SoC dtb
KernelDecompress failed: Invalid Parameter Kernel TextOffset does not match
```

Only the uncompressed ARM64 header's `text_offset` field was then changed from
0 to `0x80000`; all other uncompressed Image bytes were checked identical. The
kernel was recompressed and replaced in the same image. This second image was
accepted and **booted the native kernel and custom Linux init**:

```text
[    0.000000] Linux version 7.3.0-rc7 ... #8 ... Wed Oct 21 10:32:09 ACDT 2026
[    0.000000] Machine model: Google Pixel 5a
[    0.000000] earlycon: qcom_geni0 MMIO:0x0000000000888000
[    1.167550] Run /init as init process
[    1.263416] LUMA_NATIVE_INIT_ENTERED: no persistent mounts
[    1.502471] LUMA_NATIVE_HEARTBEAT=1.37 9.36
[   81.773900] LUMA_NATIVE_HEARTBEAT=81.64 650.71
[   91.515415] LUMA_NATIVE_RECOVERY_REBOOT
[   91.544727] reboot: Restarting system
```

Keep-bootcon plus the registered UART console duplicates some log lines; these
are not separate boots. The live FDT reported initrd `0xae480000–0xae5625f4`.
The RAM test's Android slot-suffix command-line value remained `_a`, despite
the independently queried active slot `b` and UART showing `vbmeta_b` and
`dtbo_b` loading. Do not use that inherited Android argument as slot proof.

Successful image:
`~/luma-barbet-work/uart-review-20261111/native-v2-offset80000.img`
(10,719,232 bytes), SHA-256:
`9b5351f5a78ef033093134fc2643c7a5ac7ef4f40f4ecb72f3b24b707d590012`.
Raw capture: `native-v2-offset80000-uart.log` in the same private directory
(36,555 bytes, including the subsequent reboot).

This establishes kernel entry, initramfs execution, scheduling/heartbeats and
a requested reboot. It does **not** qualify storage, USB, display, touchscreen,
radio, charging or suspend. GPU SMMU probing timed out with `-110`; this minimal
kernel is not a full hardware-support build. Do not call it a working Luma OS.

### Normal slot-b partition boot verified

After the user physically returned the phone to fastboot, product `barbet`,
active slot `b`, unlocked state and `boot_b` size were queried again. The
successful image's SHA-256 and all four slot-b backup hashes/sizes were checked
again before writing. These were the only flash and boot commands:

```sh
fastboot -s 18161JECB02915 flash boot_b ~/luma-barbet-work/uart-review-20261111/native-v2-offset80000.img
fastboot -s 18161JECB02915 reboot
```

Sending and writing both returned `OKAY`. The UART capture
`native-boot-b-partition-uart.log` (57,185 bytes) independently showed ABL
`Booting from slot (b)`, loading `boot_b`, native Linux `7.3.0-rc7`, kernel
argument `androidboot.slot_suffix=_b`, `LUMA_NATIVE_INIT_ENTERED` at 1.260106
seconds, heartbeats through 81.65 seconds and the scheduled reboot at 91.50
seconds. This is a normal partition boot, not another `fastboot boot` test.

The follow-up capture `native-boot-b-followup-uart.log` (118,509 bytes) showed
further native init execution, the retry count reaching zero, ABL reporting
`Current active slot is unbootable, switch to (a)`, and the factory 4.19 kernel
booting from `a`. ADB subsequently confirmed completed stock Android startup.
The slot-a partition hashes recorded before testing have not been read back
again afterwards; only the actual recovery boot has been reverified. No write
command targeted slot `a`, vendor_boot, dtbo, vbmeta or other partitions.

Next: add a controlled interactive UART/reboot-to-fastboot path and remove or
make optional the diagnostic timer before unattended native testing. Do not
mark the slot successful merely to conceal missing hardware qualification.
Storage, USB and the remaining hardware still require separate bring-up.

This audit supplements [the previous attempt](pixel5a-bringup.md), which is
preserved as a historical record. A boot of the new mainline-based kernel has
not been demonstrated.
The earlier conclusions about kernel handoff and the point of failure remain
hypotheses. No subsystem is qualified for Luma on this device.

## Target and recovery

### UART follow-up, 2026-11-11

Read this update alongside the historical findings below. UART is now physically
connected through the Raspberry Pi Debug Probe; `tools/uart-capture.sh` and the
host-console subsection in [the bring-up notes](pixel5a-bringup.md) describe access.
Always target the Pixel explicitly with `adb -s 18161JECB02915` or
`fastboot -s 18161JECB02915`; another Android device is connected to this host.

Before testing, ADB confirmed completed stock Android startup, slot `_a`, build
`AP2A.240805.005.S4`, and unlocked state. Fastboot independently confirmed
`barbet`, slot `a`, and unlocked `yes`. The factory `boot.img` hash was rechecked
and the diagnostic's compressed kernel compared byte for byte with it.

Two **RAM-only** tests used the retained diagnostic
`~/luma-barbet-work/review-20261021/stock-linux-diagnostic-lz4-softdeps-kmsg.img`:

1. The unchanged image (`f25f8a845b04e23f042c7f268f8bbb1ac2140263b8b0b5bcd36278228cd122ea`)
   was accepted by `fastboot boot`. UART captured ABL exit and the later return
   to fastboot, but no kernel text. Silence alone did not diagnose a kernel stall.
2. Only bytes 44–1579 of that image (the v3 header command-line field) were
   replaced with:

   ```text
   rdinit=/init panic=10 loglevel=7 earlycon=msm_geni_serial,0x888000 keep_bootcon
   ```

   All other bytes were checked unchanged. This temporary image has SHA-256
   `6fe9ab2e3c0055ee4cbda54d12cb7a29bd713e1ef2ca10973d3e6778fce54994`.
   The early-console name was checked against the exact factory kernel source
   (`drivers/tty/serial/msm_geni_serial.c`, commit
   `500b3a2059c1eec0fec08ec56add06320ea901a8`), not guessed from mainline.

The second UART capture contained 84,166 bytes and established:

- `earlycon: msm_geni_serial0` at MMIO `0x888000`.
- `Run /init as init process` at 4.146 seconds and the diagnostic's
  `LUMA STOCK-KERNEL LINUX DIAGNOSTIC` marker at 4.161 seconds.
- Module loading completed at 6.165 seconds.
- USB remained unavailable: `88e8000.ssphy`, `a600000.ssusb` and the PM7250B
  PD PHY remained deferred; `UDC=none` appeared at 36.241 seconds.
- The custom init reached the end of USB setup, then requested
  `reboot: Restarting system with command 'bootloader'` at 94.196 seconds.
  Fastboot subsequently saw the Pixel again. This is direct evidence for the
  custom userspace and recovery timer, not a watchdog-timing inference.

The factory UART driver is modular (`CONFIG_SERIAL_MSM_GENI=m`) and is absent
from the diagnostic's module closure. Its built-in early console supplies an
observation channel without loading that module. This test demonstrates a
stock-kernel/Linux-userspace diagnostic, **not a native kernel boot**.

Two concrete follow-up issues were identified by inspecting retained sources:

- The native barbet DTS selects `serial0 = &uart2`, but that SoC node is
  `serial@988000` in QUP wrapper 1. The working factory console is `0x888000`
  in wrapper 0; the factory `0x988000` console is disabled. Correct the native
  console node, wrapper, clocks, IRQ and pinctrl before relying on its output.
  The downstream earlycon name above must not be assumed to work in mainline.
- The captured stock FDT supplies USB extcons from PD PHY, SMB5 charger and EUD.
  Its PD PHY also references SMB5 VBUS/VCONN and an external boost regulator.
  The minimal module closure does not include all these providers. This is a
  concrete dependency gap, not yet a proven complete explanation or fix for
  every deferred device. Do not blindly load the entire charger/power stack.

No partition was flashed, no slot selected, no vbmeta changed, and no native
image sent during these tests. Afterwards an ordinary fastboot reboot was
issued to return to stock Android. ADB subsequently confirmed
`sys.boot_completed=1`, slot `_a`, and build `AP2A.240805.005.S4`.
Verify its current state again before continuing.
Raw logs and the modified diagnostic remain outside the repository under
`/tmp/opencode/barbet-*20261111.log` and
`/tmp/opencode/barbet-stock-diag-earlycon.img`; these are private, temporary
evidence, not release artifacts.

The supplied [Ghost status page](https://ghost.5p00ky.dev/#status) claims mainline
Pixel 5a boot, display/touch/GPU and Wi-Fi. Its
[project page](https://5p00ky.dev/projects/ghost) explicitly says source is not
public yet, and the status page offers no downloadable build. These claims
are useful leads but do not satisfy the known-good boot or source-review gate.
The supplied Reddit post (`1xasd7q` in `r/mobilelinux`) could not be retrieved;
its JSON endpoint returned HTTP 403. The user supplied an excerpt from the
author: the kernel work is a small series on top of `lazybrar/avicii-mainline`,
mostly board device tree, a panel driver, a touchscreen driver and small fixes.
This is the same experimental SoC base already retained here; no sources or
boot instructions were supplied. Treat the excerpt as an attributed lead, not
independent validation or a reproducible control.

Next: review a native boot path with the corrected console, DTB selection and
memory placement. Keep the existing no-vbmeta-workaround gate below in force;
UART availability does not authorize bypassing it.

### Continued UART investigation: console correction and RAM DTB control

The isolated experiment is retained outside Git at
`~/luma-barbet-work/uart-review-20261111/` (private directory). The original
kernel source and shared SM7250 DTSI were not edited. This directory contains
the corrected **board-only** DTS, `barbet-console.diff`, `check-console.py`,
compiled DTBs, RAM diagnostic images, and private UART captures. No experimental
patch has been integrated into the repository's device profiles.

The compiled-console check failed against the original DTB because its selected
console resolved to `/soc@0/geniqup@9c0000/serial@988000`. The isolated correction
selects a new `barbet_uart2` node under `&qupv3_id_0`, enables that wrapper,
disables the wrong `&uart2`, and retains the verified GPIO36/37 `qup02` pinmux.
The corrected compiled DTB passed checks for MMIO `0x888000`, wrapper `0x8c0000`,
`GCC_QUPV3_WRAP0_S2_CLK` (mainline clock ID 59), GIC SPI 603 level-high,
console compatible, pinctrl and enabled parent/child status. It has SHA-256
`41deeb2b425af6f82514f6d8ac7f5864820416264a1a8a683bee277bb9ef13c0`.
Both original and corrected compilations emitted the same two warning classes:
the intentionally unitless `/memory` node and the pre-existing DSI address-cell
warning. This is offline validation, not a tested on-phone native UART fix.

The retained mainline driver declares earlycon name `qcom_geni`; a future
native test should use `earlycon=qcom_geni,0x888000 keep_bootcon`, not the
downstream name `msm_geni_serial`. Its interconnect providers must also be
enabled; use the audit's interconnect-enabled configuration, not the old console
configuration that disables `CONFIG_INTERCONNECT_QCOM_SM7250`.

Two additional **factory-kernel, RAM-only** v2 controls established DTB selection:

1. A v2 image retained the exact factory compressed kernel, combined the factory
   vendor ramdisk with the existing diagnostic's legacy-LZ4 ramdisk, and supplied
   the factory base DTB with a model marker. Fastboot accepted it; custom Linux
   init ran and requested return to fastboot at about 94 seconds. The model
   marker was absent, so this first test alone did not prove DTB selection.
   Image SHA-256:
   `2dde3959d87b32999065bfbad3f0e43ba7bb9d6cc394a38ba6c255b83ec65727`.
2. The next control added root property
   `luma,ram-dtb-probe = "RAM-DTB-PROBE-20261111"` to that factory DTB, and added
   a single init command to read it from the live `/sys/firmware/devicetree/base`
   tree. Shell syntax and legacy-LZ4 round-trip were checked; the factory kernel
   payload was checked unchanged. UART printed:

   ```text
   [    4.176297] ... LUMA_LIVE_DTB_MARKER=RAM-DTB-PROBE-20261111
   [   94.209998] ... reboot: Restarting system with command 'bootloader'
   ```

   Image SHA-256:
   `7999a88cb15603994a6fce2e88a958f4f839b6c7f60a9b4c2022798011c9b384`.
   The UART capture contains 84,163 bytes. Fastboot afterwards confirmed slot
   `a`. This proves RAM-supplied v2 DTB selection; it does not prove native
   kernel entry or arbitrary image placement is correct.

The v2 controls used 4096-byte pages, base 0, kernel offset `0x8000`, ramdisk
offset `0x1000000`, DTB offset `0x1f00000`, tags offset `0x100`, OS version
14.0.0 and patch level 2024-08, matching the retained layout and factory OS
fields. These are header values, **not observed physical runtime addresses**.
Do not extrapolate overlap safety to the larger decompressed native kernel.

The stock overlay remains a separate, reproduced blocker. UART identifies DTBO
index 29. That entry was extracted from the hash-verified factory `dtbo.img`;
entry SHA-256 is
`806b2a114965bebdfcd585e6589fc2e1a8ab80a6fac0a04fcfaff6eab6f832d8`.
The retained kernel's `scripts/dtc/fdtoverlay` applied it successfully to the
factory base DTB. Applying it to the corrected native DTB failed. Recompiling
the native DTB with `-@` removed the missing-symbol-table ambiguity, but overlay
application still failed with `FDT_ERR_NOTFOUND`; 158 overlay fixup symbols are
absent from that native tree. The stock overlay also supplies the observed
stock model name. Do not add dummy aliases or stock downstream device nodes
just to make overlay application succeed.

No partitions were flashed, no slot selected and no native kernel sent in
these controls. After the final test, an ordinary reboot returned to stock
Android; ADB confirmed `sys.boot_completed=1`, slot `_a` and build
`AP2A.240805.005.S4`.
The remaining gate is a reviewed native path that handles stock-overlay
selection and arm64 physical placement; availability of v2 RAM DTBs alone does
not clear it. No full kernel build or on-device native qualification was
performed during this continuation.

Read-only ADB queries on the connected phone returned Pixel 5a, `barbet`, SKU
`G4S1M`, build `AP2A.240805.005.S4`, slot `_a`, and unlocked state
`ro.boot.flash.locked=0`. Fastboot independently returned product `barbet`,
bootloader `b9-0.6-10898944`, current slot `a`, and unlocked `yes`.
Slot `a` was successful and not unbootable. Slot `b` was not successful and
not unbootable; this does not establish that slot `b` boots.

The platform is Qualcomm SM7250 / Snapdragon 765G. Japan SKU and previous
revision evidence must not be generalized to other Pixel models.

The documented unlock procedure is to enable OEM unlocking, check unlock
ability, enter the bootloader, issue `fastboot flashing unlock`, and confirm
on the phone. Unlocking erases user data. This device is already unlocked;
unlocking was not repeated. See [AOSP's unlock procedure](https://source.android.com/docs/core/architecture/bootloader/locking_unlocking).

The retained [Google factory ZIP](https://dl.google.com/dl/android/aosp/barbet-ap2a.240805.005.s4-factory-eb064cd6.zip)
was hashed again:

```text
eb064cd61c8e95ace5f2e2a3c2f29d72fb716f2cc4fa12d742c2fae9c934002c
```

Its extracted inner image ZIP was compared with the entry in that verified
outer archive and matched, with SHA-256:

```text
a828a9190f8ea8b8f7e440717d46437269531d1aa533bb4a8d113a2273705cc6
```

Each of these extracted images was hashed and compared with its entry in the
inner ZIP. All matched, including the hashes recorded in the previous attempt:

| File | SHA-256 |
| --- | --- |
| `boot.img` | `a0db60a9740ff701c331ff96f28c2358a56f57147482a6283bb0e381af7147f5` |
| `vendor_boot.img` | `14e851027ffcadba26e4e5d268f165eec32e16c96a184b9a4087c866932edc07` |
| `dtbo.img` | `3a7ca01344960a74602b88c7818e63928fce13c1f053141ea743c3e508495ae0` |
| `modem.img` | `f262738a3199b531a092d2d43cd6affd0795460d08aa1a7e86830e066fc6e3d0` |
| `vendor.img` | `e7759089b680a5eb68d7e17af4c6dc7058764b195c9a25ee845eeda1b33e4ee5` |
| `vbmeta.img` | `1b626ea81425ed5f6e9121795f958bc7be637efe68db3e0dc38fd48fc1df7874` |
| `vbmeta_system.img` | `a1b79b2a35b00a542e73f6734e0a2f700f0b112faa8d0536bb3043285c376d87` |

The local `stock-device/boot_a.img`, `vendor_boot_a.img`, `dtbo_a.img`, and
`vbmeta_a.img` are all zero bytes. They are not partition backups. The recovery
asset is the factory archive, not those files. A verified archive alone does
not prove a restore procedure or authorize running `flash-all.sh`.

Firmware extraction for a later native boot must use this exact verified
archive, preserve file paths, and record individual firmware hashes. Hashing
`modem.img` and `vendor.img` does not establish a firmware-file manifest.
No Nord firmware has been admitted for the Pixel.

## Kernel and port sources

- The retained `avicii-mainline` Git checkout is clean at
  `780934bbcc758694a9e4e8950eed9c040725df4d`.
  [Pinned source](https://github.com/lazybrar/avicii-mainline/tree/780934bbcc758694a9e4e8950eed9c040725df4d).
  It is a useful experimental SM7250 implementation, not a qualified Pixel
  board port. Keep its patch headers, licenses, and `patches/COMMITS.txt`.
- `git ls-remote` against Torvalds' kernel.org repository resolved
  `v7.3-rc7^{}` to `24a19b953e2a3d0ea5a8c4951a0ab8f32720c907`, matching the
  earlier base pin. The retained kernel directory has no `.git`; this check
  alone does not authenticate all files or establish the complete local diff.
- The retained `linux-7.3-rc7.tar.gz` is truncated: Python's tar reader raises
  `EOFError`. The separate `.googlesource.tar.gz` can be enumerated. Successful
  decompression alone does not prove that archive matches the base commit.
- Current pmaports was inspected via Git at exact commit
  `02b84c93abb1d510de752ebb2143d83fc55f68cd`. Its complete tracked path list
  contains no `barbet`, `redfin`, `bramble`, `avicii`, or `sm7250` paths.
  [Pinned package source](https://gitlab.postmarketos.org/postmarketOS/pmaports/-/tree/02b84c93abb1d510de752ebb2143d83fc55f68cd).
  This does not rule out external work, but supplies no known-good Pixel 5a
  postmarketOS image for the required boot control.
- SM7250 work is still being reviewed upstream. The
  [v10 cover letter](https://lists.openwall.net/linux-kernel/2026/10/15/466)
  identifies message `20261015051926.13752-1-sreeshankar0910@gmail.com`;
  the [base DTS patch](https://lists.openwall.net/linux-kernel/2026/10/15/474)
  is message `20261015051926.13752-7-sreeshankar0910@gmail.com`.
  Submission is not evidence of acceptance or Pixel support. Compare this
  serial-console baseline with the retained SoC DTS before a rebase.

## Findings from the retained kernel

The modem is **disabled**: `sm7250.dtsi` sets `mpss: remoteproc@4080000` to
`status = "disabled"`; the barbet DTS has no `&mpss` override. The reserved
RMTFS memory node does not enable a modem. There is no native QRTR/QMI or
ModemManager evidence, and no demonstrated calls, texts, or mobile data.

The console configuration has `CONFIG_INTERCONNECT=y` but disables
`CONFIG_INTERCONNECT_QCOM_SM7250`. Its USB node references SM7250 interconnect
providers. `dwc3_qcom_interconnect_init()` obtains those paths with
`of_icc_get()` and propagates errors, so an unavailable provider can block
USB probing independently of whether init ran. The previous claim that
missing ACM proves a stall before init is not established.

An isolated copy of the retained source was built with this single config
change:

```text
CONFIG_INTERCONNECT_QCOM_SM7250=y
```

The config diff was checked to contain only that change. These commands
completed successfully with `ARCH=arm64 LLVM=1`:

```sh
make olddefconfig
make -j8 drivers/interconnect/qcom/sm7250.o qcom/sm7250-google-barbet.dtb
make -j8 Image qcom/sm7250-google-barbet.dtb
```

This is an offline compilation result, not an on-phone fix. The copied DTS
was unchanged. Outputs and hashes are outside the repository under the
operator's `luma-barbet-work/review-20261021/` directory. No modules were
loaded on the phone.

The retained `console-Image` is 27,150,344 bytes, whereas the old document
records 26,150,344 bytes. Its arm64 header declares `image_size=0x1a60000`
(27,656,192 bytes), `text_offset=0x80000`, and flags `0xa`.
The retained console vendor_boot declares kernel address `0x8000`, ramdisk
address `0x1000000`, and DTB address `0x1f00000`. Compressed image size alone
does not exclude overlap. Actual placement, decompression and relocation by
ABL must be established before choosing load addresses. Header modification
alone is not proof that the arm64 entry contract is satisfied.

The barbet DTS also has a zero-sized memory placeholder and depends on a
bootloader fixup. Its successful fixup has not been captured. A splash screen
and empty pstore cannot distinguish image rejection, entry failure, memory
corruption, driver deferral, a later hang, or a different ramoops layout.

## RAM-only controls and remaining gates

1. The exact verified factory `boot.img` was sent with `fastboot boot`.
   Fastboot returned success; ADB subsequently reported
   `sys.boot_completed=1`, boot animation stopped, and slot `_a`.
2. A second control retained the factory boot header, kernel, ramdisk and
   payload padding byte for byte, but omitted trailing AVB metadata and
   partition padding. The resulting 14,102,528-byte image has SHA-256
   `e02c7017f63376b1e6f75d7e24af7340847efe218287b5db82f18fcd806eb616`.
   It also RAM-booted and completed Android startup on slot `_a`.
   This disproves a blanket requirement for a Google signature in the
   transferred boot file. It does not prove a changed payload is accepted:
   the payload still matches stock, and installed vbmeta was not changed.
3. A third stock control added only `loglevel=7` to that header. Its SHA-256 is
   `5138d3707f5fbbfbc61c28433c2ebc06f44dde30997fd256e55453bb0bf7e5d6`.
   Fastboot accepted it; Android completed startup on slot `_a`. Thus a changed
   transferred payload is accepted too. This does not establish that ABL passed
   the changed command line to the kernel, or that another kernel/DTB boots.

The stock kernel's embedded config was extracted successfully. The retained
uncompressed stock Image was compared with a fresh decompression of the exact
factory kernel payload and matched SHA-256
`cdfe189ce7c69687471d5dd31d64c8060b223dfe14f512b4487458662349b1c1`.
It disables `DEVTMPFS` and USB configfs ACM, enables USB configfs NCM, and
builds the DWC3 controller drivers as modules. The previous ACM initramfs
cannot be assumed to work with this kernel. Its watchdog command also calls
`busybox-extras watchdog`, whereas the retained Alpine watchdog applet is
provided by the main BusyBox binary.

All 220 retained module/metadata entries were checked byte for byte against
the vendor cpio freshly unpacked from the verified factory `vendor_boot.img`;
none differed. Factory module vermagic matches kernel release
`4.19.294-g500b3a2059c1-ab11922802`.

A fourth RAM-only diagnostic kept that factory kernel and used a small Linux
ramdisk with Alpine BusyBox/musl and 41 matching factory modules selected for
USB and its dependencies. No Android executable is included in this ramdisk;
its init has no persistent filesystem mounts. A recovery timer is placed
before module loading. Its image SHA-256 is
`0c810f4e1bdcdf7473601860e5c3775995b33a5446c3a09eaf38d9cb476e91d2`.
Image unpacking, kernel byte equality, cpio inventory and init shell syntax
were checked before the test. Fastboot accepted it, but no diagnostic USB
device had appeared at the initial observation. Init execution remains
unproven. This stock-kernel diagnostic is not the missing known-good
postmarketOS control or a native kernel boot.

The gzip diagnostic remained on the black Google logo and later returned to
stock Android (`sys.boot_completed=1`, slot `_a`). A concrete packaging issue
was then found: factory vendor ramdisk legacy LZ4 has no zero terminator, and
the kernel's LZ4 decoder treats the following gzip bytes as an LZ4 chunk.
The source was checked at the exact factory kernel commit
[`500b3a2059c1eec0fec08ec56add06320ea901a8`](https://android.googlesource.com/kernel/msm/+/500b3a2059c1eec0fec08ec56add06320ea901a8/lib/decompress_unlz4.c).
Following that decoder's chunk logic with liblz4 reproduced failure exactly
at byte 30,355,171, the end of the vendor ramdisk. This is a local reproduction,
not a captured on-phone kernel error. A legacy-LZ4 diagnostic ramdisk instead
decoded successfully after the vendor stream, and the result matched the two
expected cpio archives byte for byte. The compression-only replacement image
has SHA-256
`672a4373638980def09e07adad02f89a8bdd2f56a5266b5df87516372b09f6f8`.
Fastboot accepted that image too, but diagnostic USB was still absent during
the observation. Compression correctness alone has not established init or
USB operation.

A further control was built and RAM-booted: the exact
stock kernel with a legacy-LZ4 cpio containing a static AArch64 `/init` only.
Its source, `init-reboot-probe.S`, waits 20 seconds, requests a Linux reboot,
and exits if that request fails. ELF headers were checked for AArch64, static
linking and a valid entry point. This removes the shell, musl, modules and USB
from the init-execution experiment. The image SHA-256 is
`0c2a385bfef896d0db96e932941d4f0040123d4d753730cd37639cec7b7ceefb`.
The phone returned to Android automatically approximately 47.7 seconds after
the USB observation began. ADB confirmed successful startup on slot `_a`.
Android reported boot reason `watchdog`; the return alone is not sufficient
to distinguish the intended reboot from a watchdog timeout.

An otherwise identical static init was then built with a five-second delay.
Its ELF differs by exactly one byte, the sleep duration. Image SHA-256:
`7eb1e589cd72c75e250db574e60c2893a651034b5a1118a7ed1f2d8261f40c0b`.
Android USB appeared 35.5 seconds after fastboot completed. A shell-only
control used Alpine BusyBox/musl under `/diag`, slept five seconds, and
requested a reboot without any module loading. Its image SHA-256 is
`1b8fe867c4a0a31c8fcc7a52a84b2fd76f600759a1b8be0c13ba75958ec2d7da`;
Android USB appeared after 35.6 seconds. ADB confirmed completed Android boots
on `_a` after both tests. The timing change supports execution of the custom
init; matching static and shell timings support the userspace runtime working.
These are stock-kernel controls, not a boot of the new kernel or a qualified
postmarketOS image. No diagnostic USB transport has been demonstrated yet.

Read-only `dumpsys dropbox --print SYSTEM_LAST_KMSG` succeeded, but its available
entry was from 08:57:38, before these controls. It cannot explain these tests.
Private captures remain outside the repository and must not be published.

The exact factory module metadata also contains soft dependencies absent from
`modules.dep`. In particular, SMEM declares `pre: qcom_hwspinlock`; that
provider declares `pre: qcom-cpufreq-hw`. PDC declares early watchdog and random
providers. A direct insmod sequence based only on `modules.dep` omits these.
The diagnostic builder now has an explicit option to honor the factory
`modules.softdep`, and fails on missing entries in the selected closure rather
than force-loading anything. Its resulting 44-module USB diagnostic has hash
`491299d8ece6d9c08f13265716ab2ec00e6eac743a0ec1c9b3606bcc042532c9`.
No storage or modem loader module is selected. On-phone acceptance remains to
be observed for this variant.

## Rooted stock control and live hardware evidence

The 44-module diagnostic did not expose NCM USB. The phone subsequently
returned to Android at a time consistent with its three-minute recovery timer
plus Android startup. This is not a USB success.

The existing `magisk27-boot-final.img` was inspected: its kernel payload is
byte-identical to the verified factory kernel. Its boot payload, without
trailing partition padding or AVB metadata, was RAM-booted successfully.
Payload SHA-256:
`2246cea559bf4c3e491bd4cdb1fcf26ac76201b29d2b36a25033be6569e6f908`.
ADB `su -c id` returned UID 0 in the Magisk domain. This was a diagnostic
stock Android boot, not Luma's control plane. Existing Magisk modules can run
and write their diagnostic files during such a boot; no claim of zero
userdata writes is made.

Root access enabled a validated live FDT capture. Raw binary transport through
Magisk su introduced extra bytes; that capture failed dtc validation and was
marked invalid. Retransmission as base64 produced a valid 818,908-byte DTB
whose size matches its FDT header and which dtc decoded successfully. Private
FDT captures include boot-time identifiers and random seeds and are not
repository artifacts.

The bootloader-populated `/memory` describes these banks on this phone:

| Base | Size |
| --- | --- |
| `0x80000000` | `0x3cc00000` |
| `0x100000000` | `0x100000000` |
| `0xc0000000` | `0x40000000` |

In this rooted stock boot, chosen `linux,initrd-start` was `0xafe00000` and
`linux,initrd-end` was `0xb1d2359a`. `/proc/iomem` placed running kernel code
at `0xa7e80000–0xa91cffff`. This is a post-relocation observation, not proof of
the initial entry address. These values demonstrate why the header's declared
ramdisk address or the compressed kernel-to-ramdisk gap cannot by themselves
establish the actual placement or exclude overlap in a different image.

Correctly quoted root commands could read the current kernel log, live FDT,
and pstore controls. Earlier permission failures from a multi-command ADB
invocation were partly caused by remote-shell quoting: commands after an
unquoted semicolon ran outside su. Do not infer a Magisk-domain denial from
those shell-domain errors. Neither UID 0 alone nor an ADB transport exit code
proves that an individual read succeeded; validate its content and format.

The primary pstore view was empty. An attempt to select the alternate view
failed; the kernel reported `ramoops: Failed to set key, -22`. Audit commands
did not read key, IV, tag or credential files, or supply replacement
cryptographic material. The factory helper still performs its normal
key-management operations during Android startup.

One further diagnostic kept the factory kernel, honored the factory soft
dependencies, included its matching `msm-poweroff.ko`, and sent init output
to `/dev/kmsg`. A static helper used the standard Linux RESTART2 syscall with
`bootloader`; a 90-second timer requested that restart. Image SHA-256:
`f25f8a845b04e23f042c7f268f8bbb1ac2140263b8b0b5bcd36278228cd122ea`.
This variant contains 45 selected factory modules and no storage or modem
loader modules. Assembly linking, ELF headers and shell syntax were checked.
Fastboot accepted it; no NCM device appeared during the initial 55-second
observation. After the session interruption, the phone was found in fastboot
on slot `a`, consistent with the requested restart. The transition itself was
not continuously captured, so no exact restart latency is claimed.

The rooted stock payload was then RAM-booted directly from fastboot to avoid
an intervening ordinary Android boot. Root access worked, but primary pstore
remained empty and the vendor helper reported
`vendor.ramoops.decryption.error=1`. Its `vendor.ramoops.decrypted=true`
property is not proof of decryption: the verified vendor script sets it
regardless of whether `ramoops -D` succeeds. The available kernel log was
the current rooted stock boot, not the preceding diagnostic. No fresh
diagnostic log was recovered.

UART remains the next practical observation channel for the new kernel.
This audit does not prove it is the only possible channel, nor validate the
old document's cable pinout or voltage. Verify those electrical details
against an appropriate primary hardware reference before connecting an
adapter. Further unobserved native-kernel images have not been sent.

The rooted RAM session was ended with an ordinary reboot. ADB then confirmed
`sys.boot_completed=1`, slot `_a`, and build `AP2A.240805.005.S4`.
`command -v su` returned no path in that normal boot. The phone was left in
Android, not in the diagnostic ramdisk or fastboot. No partition was flashed
or slot selected during these controls.

No partition has been flashed during this audit. No slot was selected with
`set_active`, and no `vbmeta`, persist, modem storage, bootloader or partition
table write was performed. Stock Android remains a diagnostic control, not
a Luma hardware backend.

The required known-good postmarketOS boot is still unavailable. The corrected
native kernel has not been booted or flashed. Do not bypass that gate by
repeating the earlier vbmeta workaround. Before a native test, establish a
reviewed boot path with correct DTB selection and memory placement, and a
method to distinguish init execution from USB probe failure.

After a native boot is observable, test storage, display, touch, USB, charging,
Wi-Fi, Bluetooth, audio, modem, camera and suspend separately. Record exact
source/config/firmware hashes, commands, end-to-end observations and failures.
Native modem acceptance needs remoteproc startup, QRTR/QMI reachability,
network registration, data transfer, SMS in both directions, and calls with
two-way audio; IMS requirements must be recorded for the actual carrier.
Do not spoof identities or force mismatched modules.

No success report has been posted to the Hub. A Devices report should follow
verified results and state unsupported functions plainly; a source build or
stock Android boot is not a working Luma port.

## 2026-11-11 native kernel, UART shell and first display/UFS probe

This section supersedes earlier statements that the corrected native kernel had
not booted. Work stayed on slot `b` for RAM-supplied images; slot `a` stock
Android remains the recovery path. No bootloader, modem/persist storage,
partition table, rollback metadata or userdata was written.

Booting was made observable over the USB-C SBU UART (`/dev/ttyPICO`, 115200
8N1). A RAM-only initramfs runs BusyBox `sh` on `ttyMSM0`. Host-side capture
uses `tools/uart-capture.sh`.

Observed with the native 7.3.0-rc7 image:

- `fastboot boot` requires the arm64 Image `text_offset` field to be `0x80000`.
  An Image built with the tree default `0` is rejected with
  `KernelDecompress failed: Invalid Parameter Kernel TextOffset does not match`.
  The image builder patches that header field before LZ4 compression.
- Native init reaches `/init`, mount, interactive shell and heartbeats.
- The audited diagnostic kernel had networking and most subsystem drivers
  disabled. An isolated build with `CONFIG_NET`, `UNIX`, `INET`, `PACKET`,
  USB configfs ECM/NCM, DRM/MSM, `SM_DISPCC_7250`, UFS, and the Samsung
  sofef00 panel driver boots and probes more hardware.
- UFS probes as `ufshcd-qcom 1d84000.ufshc` with six LUNs and creates
  `sda`..`sdf`. This was read-only inspection of partition tables; nothing was
  mounted read-write or written.
- MDSS/DPU probes (`dpu hardware revision 0x60010000`), `card0`,
  `card0-DSI-1`, `renderD128` and the panel backlight device
  `ae94000.dsi.0` appear. The panel driver binds the DSI host.
- The DSI PLL does not lock yet (`DSI PLL(0) lock failed, status=0x00000000`).
  No modeset has been proven. A display image with fbdev emulation was built
  but not yet confirmed on screen.
- The apps SMMU reports a context fault during continuous-splash handoff
  (`iova=0xa0115c00`, `SID=0xf40`). The GPU SMMU still fails with `-110`.
- A signed Fedora 44 AArch64 base runtime from the repository's existing RPM
  lock executes on the device: Bash 5.3.9 and glibc 2.43 report Fedora 44 via
  `chroot`. This proves the CPU/ABI/kernel userspace prerequisite only; it is
  not Luma and not the mobile session.
- Two USB transports remain unproven: the configfs ECM gadget was not yet
  exercised on the networking-enabled kernel, and the kernel-side
  `syscon-reboot-mode`/`reboot bootloader` path did not return to fastboot.
  The PMIC PON restart-reason path (`mode-bootloader = <0x02>` on `&pon`) is
  prepared for the next image.
- Recovery cost is real: a RAM-booted native image that does not mark its slot
  successful leaves the device without USB, and ABL only returns to stock
  Android after the flashed slot-`b` image fails and the slot is marked
  unbootable. This took about 4-5 minutes per cycle and should be reduced
  before further experiments.

Packed ramdisk and kernel evidence, image hashes and raw UART logs are kept
outside Git under `~/luma-barbet-work/luma-userspace-20261111/`. Nothing in
this section qualifies display, touch, USB, radio, charging or suspend, and
none of it is a working Luma port.

## 2026-11-11 display modeset, framebuffer console and USB network

RAM boot only, active slot `b`; slot `a` stock Android untouched. Image
`fbcon-v3.img` (kernel with VT + fbcon + DRM fbdev emulation, DTB with
display/PON/IMEM nodes). Same PON restart-reason path as before.

Observed over UART:

- A real modeset now occurs. `fb0: msmdrmfb frame buffer device`,
  `Console: switching to colour frame buffer device 135x142`,
  `card0-DSI-1` reports `connected`/`enabled`, `fb0/blank` is `4`.
- The DSI PLL locks on the real mode: `dsi0vco_clk` enabled at
  1,046,153,906 Hz, `dsi0_phy_pll_out_dsiclk` 174,358,985 Hz, byte clock
  130,769,238 Hz, esc/ahb 19.2 MHz.
- The earlier `DSI PLL(0) lock failed, status=0x00000000` at ~1.3 s is an
  unused-clock prepare with no requested rate, not a modeset failure. It is
  followed by `dsi0_phy_pll_out_dsiclk already disabled`.
- Panel backlight accepts brightness (220 and 1023 read back). Whether the
  panel visibly shows the console has not been confirmed by a person yet.
- Defect: the sofef00 panel driver exposes a single fixed mode, 1080x2280
  (ams628nw01, Pixel 4). Barbet's sofef01 panel is 1080x2400, so the console
  runs at the wrong resolution. A proper sofef01 mode is still needed.
- USB configfs ECM works on the networking kernel. The gadget enumerates as
  `1d6b:0104` `Luma`/`sm7250-barbet`, host binds `cdc_ether`, UDC state
  `configured`, phone `usb0` is `192.168.77.2/24` and host ping both ways
  succeeds at 1.4-3.3 ms. Host address assignment needed administrator
  rights; it was applied with a privileged host-network container.
- An earlier ping to `192.168.77.2` that "succeeded" before the host address
  was assigned was routed via the LAN gateway and did not test the phone.
- UFS still probes six LUNs and `sda` reports 128 GB. No writes.

A blocking `nc` client was run in the foreground of the UART shell; because
that shell is started with `setsid`, it has no controlling terminal and
Ctrl-C could not interrupt it, leaving the shell blocked. The kernel stayed
healthy and answered ping. Background/`setsid` servers only from now on.

No change to the audit's earlier conclusion: display is now proven at the
DSI/DRM level but not yet a usable Luma session, and touch, radio, charging
and suspend remain unproven.

## 2026-11-11 Luma initramfs starts on barbet (RAM boot, slot b)

Booting real Luma userspace begins with the initramfs, so the first step was
to run Luma's own initramfs on this kernel rather than ours.

Kernel prerequisites added to the isolated build (all built in, since a RAM
initramfs cannot load modules):

- `EXT4_FS`, `MSDOS_FS` (LumaRoot / LumaBoot), `FUSE_FS`, `OVERLAY_FS`,
  `BLK_DEV_DM`, `DM_CRYPT`, `AUTOFS_FS`, `BPF_SYSCALL`, `KEYS`,
  `CGROUP_BPF`, `BLK_CGROUP`, `TUN`-class networking options where the
  symbol exists, `IPV6_MULTIPLE_TABLES`, `NETFILTER`, `NET_SCHED`,
  `BRIDGE`, `POSIX_MQUEUE`, `USERFAULTFD`, `EXT4_FS_POSIX_ACL`,
  `EXT4_FS_SECURITY`, `SECURITY`, `AUDIT`.
- SELinux is deliberately not enabled yet; the deployment carries labels but
  a first boot is easier to read without policy enforcement. This is a
  follow-up, not a claim that Luma will run unconfined.

Boot image (`build-luma-boot.sh` in the experiment directory):
kernel `Image` with `text_offset` patched to `0x80000`, lz4-compressed
(ABL rejects an uncompressed `Image` with `kernel compression not supported`),
our board DTB, and the Alpha 1 ramdisk (postmarketOS initramfs + Luma overlay:
`ostree-prepare-root`, Fedora 44 libraries, plymouth theme) followed by a
fourth cpio archive holding the shared OSTree boot resolver
(`scripts/mobile/common/initramfs`) and Luma's `init_functions.sh`, which
keeps `/boot` on the root filesystem. The fp6 modem-store seed is deliberately
omitted: `modemst1`/`modemst2` belong to this phone's Android installation.
Command line uses `pmos_root_uuid=03797c05-...`, `pmos_boot_uuid=032D-57AD`
and `ostree=/ostree/root.a`; ABL appends `androidboot.slot_suffix=_b`.

Observed over UART (`luma-ramboot2-uart.log`):

- Kernel 7.3.0-rc7 boots, `Machine model: Google Pixel 5a`, command line
  carries `androidboot.slot_suffix=_b` exactly once.
- UFS exposes `sda` with all 19 partitions by name: `boot_a/boot_b` 96 MiB,
  `dtbo_a/dtbo_b` 16 MiB, `super` 9.1 GiB, `userdata` 228,818,904 sectors
  (117.2 GB).
- The Luma initramfs runs and the shared resolver executes:
  `luma-ostree: root partition not found; booting the existing slot link`.
  That is correct: no LumaRoot exists yet, because `userdata` still holds
  Android's filesystem.
- USB networking comes up as `usb0`; the initramfs DHCP server fails with
  `Unable to bind to interface: Protocol not available`, so the pmOS USB
  network is not usable in this image yet.
- The initramfs drops to the postmarketOS debug shell on UART, which gives a
  working shell inside Luma's initramfs (`uname -r` 7.3.0-rc7).

Open issue: the 7.3 reboot-mode class exposes only the read-only
`reboot_modes` list, so the earlier `echo bootloader > .../reboot_mode` exit
does not exist here. The debug shell has no `devmem` applet. Leaving a RAM
boot currently means restarting and letting ABL fall back to slot `a`
Android, then `adb reboot bootloader`. A deterministic exit (writing the
syscon cookie at `0x146ab65c` or a small reboot-mode store attribute) is
needed to keep bring-up cycles short.

### Getting the generic ARM commit: CDN throughput notes

The public OSTree repository (`https://dl.simplyluma.com/os/repo`, `archive-z2`,
`indexed-deltas=true`) is served per-connection from
`media.simplyluma.com` at roughly 350-500 kB/s, and the pull needs one request
per object (tens of thousands of small objects). Measured facts:

- default `ostree pull` (8 outstanding requests): ~6-10 MB/min;
- `--max-outstanding-fetcher-requests=256 --disable-fsync`: ~25-75 MB/min;
- 16 parallel `curl` connections to different objects: ~37 MB/min, so the
  server caps aggregate throughput per client rather than the client's
  concurrency. Concurrency still helps because the per-object latency is
  ~1.5 s.
- The fork's release key must be imported before pulling:
  `ostree remote gpg-import --repo=REPO -k config/os/keys/luma-os-release.asc luma`.
  A hand-placed `keyring/<KEYID>.pub` is ignored by current OSTree; it uses
  `<remote>.trustedkeys.gpg` in the repository.
- `docker exec ... &` does not survive the exec, so long pulls must run under
  `docker exec -d`.

#### How the generic userdata image is found on barbet

The generic phone userdata image is a GPT with 4096-byte sectors holding
`LumaBoot` (FAT32, 480 MiB at 1 MiB, volume ID `032D-57AD`) and `LumaRoot`
(ext4, UUID `03797c05-cf87-4eda-adc4-6ce111baf2d3`). barbet's UFS reports
4096-byte logical blocks (`[sda] 31211520 4096-byte logical blocks: (128
GB/119 GiB)`), so the image's sector size matches the device: `fdisk -l` on
the `userdata` partition reads the primary GPT at its 4096-byte offset, and
`parted` can rewrite it later without a sector-size mismatch.

The postmarketOS initramfs finds it as a nested partition table
(`mount_subpartitions`): the `userdata` partition is loop-mounted with
`--sector-size 4096 -P`, `LumaRoot` is found by UUID, and
`resize_root_partition` then extends the inner GPT to 100% of the loop device
(that is, the whole 117.2 GB `userdata` partition) and grows ext4 offline, so
the 12 GiB generic image fills the phone on first boot. That path needs the
`fdisk -l` partition count to be exactly 2.

Rehearsed off-device with a composed image (`rehearse-userdata-gpt.sh`,
`unsimg.py`): Android sparse output expanded to raw, loop-mounted with
`-b 4096`, `fdisk -l` reports exactly 2 partitions (480 MiB EFI System +
Linux filesystem), `blkid --uuid` finds `LumaRoot`, the deployment mounts with
`/ostree/root.a` and `/ostree/root.b`, and `ostree admin deploy` writes
`boot/loader` entries. No on-device write is needed to validate this.

## 2026-11-12 Real Luma runs on barbet, natively from slot b

First successful boot of real Luma userspace on the Pixel 5a (`userdata`
flashed, `boot_b` written, ABL boots slot b).

### What was written

- `userdata` (`/dev/sda19`, 117.2 GB): the published Luma ARM64 system image,
  `luma-mobile-alpha1-fp6-userdata.sparse.zst` (1,104,575,926 bytes, SHA256
  `75ab6f59c6013017a4fe32d68768f20996b4c57aaf3bbc7a7e5da13c67ba251f`,
  matching the published `SHA256SUMS`). After decompression it is a 16 GiB
  Android sparse image with a 4096-byte-sector GPT, an empty 480 MiB
  `LumaBoot` (ID `032D-57AD`) and `LumaRoot` (UUID
  `03797c05-cf87-4eda-adc4-6ce111baf2d3`) holding deployment
  `97268a77ca69c7853298590406da8bb7d3e9c012070d369a5c375b04d792b066.0`
  ("Luma (Prairie, Beta 0, Nightly 20261015)") with **both** `/ostree/root.a`
  and `/ostree/root.b` pointing at it. `fastboot flash userdata` wrote the 13
  sparse chunks in 363 s.
- `boot_b` (96 MiB): our barbet boot image with the AVB footer,
  `luma-barbet-boot-v4-avb.img` (SHA256
  `b80c7f9ca12397f60a020a2aba8299455b05c0fe59776e7e143b95b83ee4dc1e`,
  `avbtool`: algorithm NONE, 100663296 bytes, rollback index 1783209600).
  Kernel `Image` (7.3.0-rc7, `text_offset=0x80000`, lz4), our board DTB and
  the Alpha 1 ramdisk + the shared OSTree resolver overlay.
- Slot a, `boot_a`, `dtbo`, `vbmeta`, the bootloader and the modem/persist
  partitions were not written.

### Evidence (UART `luma-nativeboot-uart.log`)

- ABL: `Booting from slot (b)`, `vbmeta_b ... VERIFICATION_DISABLED`,
  `boot_b: Loading entire partition`, `post boot state orange`.
- Kernel: `Machine model: Google Pixel 5a`, `Linux luma 7.3.0-rc7 #14 SMP
  PREEMPT aarch64`, command line carries our console arguments and
  `ostree=/ostree/root.a`.
- `ostree admin status`: `* luma 97268a77...0`, version
  `1.0.0-nightly.20261015.21`, origin refspec
  `luma:luma/1/aarch64/fairphone-fp6/nightly`.
- `/` and `/boot` are `/dev/loop0p2`, 107 GiB, 3.4 GiB used: the initramfs
  grew the inner GPT from 16 GiB to the full `userdata` partition and resized
  ext4 on first boot.
- `systemctl is-system-running` = `degraded`. Failed units:
  `greetd.service` (greeter exits without creating a session),
  `luma-fp6-carrier-pdc`, `luma-fp6-modem-store`,
  `luma-fp6-primary-gw-session` (fp6-only), `tqftpserv`.
- Display is up: `Console: switching to colour frame buffer device 135x150`
  and `msm_dpu ... [drm] fb0: msmdrmfb frame buffer device`, `card0-DSI-1`
  is `connected`, `/dev/dri/renderD128` exists, backlight is 512.
  `DSI PLL(0) lock failed, status=0x00000000` appears on every boot, including
  the earlier working display proof, and is followed by the framebuffer; it is
  not a regression from the Luma boot.

### Image identity, stated plainly

This is the published Luma ARM64 userspace, but it is the **fp6-branded**
build: `IMAGE_ID=luma-mobile-fp6`, origin refspec
`luma:luma/1/aarch64/fairphone-fp6/nightly`, and it ships fp6 kernel modules
and fp6-only services (the failures above). It is not the device-neutral
generic commit (`luma/1/aarch64/nightly`). The generic nightly pull is still
running separately; deploying it later would remove the fp6 branding and
services.

### Getting to slot b without fastboot

`systemctl reboot bootloader` is not accepted by this systemd
(`systemctl: Too many arguments.`), and the 7.3 reboot-mode class has no
writable attribute, so a running Luma cannot ask ABL for fastboot. Instead:

1. the Alpha 1 initramfs' USB-NCM gadget stays up in userspace; the host
   appears at 172.16.42.2 and the phone at 172.16.42.1;
2. `python3 -m http.server 8000 --bind 172.16.42.2` on the host, then
   `curl -o /tmp/luma-boot.img http://172.16.42.2:8000/...` on the phone
   (96 MiB in 3 s, ~31 MB/s);
3. `sudo dd if=/tmp/luma-boot.img of=/dev/disk/by-partlabel/boot_b bs=1M
   conv=fsync` (96 MiB, ~200 MB/s), read back and `sha256sum` to confirm;
4. `sudo systemctl reboot` boots the new image from slot b.

This replaces `fastboot flash boot_b` and makes bring-up cycles possible
without leaving the running system. Caveat: after the first cold boot the
console is flooded by audit printk and drops UART input; `dmesg -n 1` and
typing with a quiet-line gate make it usable.

### First-boot session state, for the next step

`/etc/greetd/config.toml` on the image boots `[initial_session] command =
"/usr/libexec/luma-phosh-session"` (user `luma`, Phosh/Phoc, persistent
compositor) and keeps `[default_session] command =
"/usr/libexec/luma-presence-compositor"` (user `greetd`) as the recovery
greeter. `greetd.service` fails with `error: check_children: greeter exited
without creating a session` and hits its restart limit. `loginctl` shows only
the ttyMSM0 shell session; `/dev/input` is empty (no touch or key driver has
bound), and there is no GPU driver (arm-smmu -110), so the compositor's
graphical start is the open question. This is the next task; the framebuffer,
backlight and DSI link are otherwise known good.

## 2026-11-12 Device-neutral ARM commit deployed on the phone

The generic ARM commit (no `org.projectluma.hardware-target`) is now the
running deployment on barbet, installed in place over the working fp6 image
rather than by reflashing userdata.

### Pull and deploy

- Pulled `d9d453aca66336143a8dd335d3eaf5c0b22a65329664bcd3df69009d1779517e`
  (`1.0.0-nightly.20261110.4`) from the local repository over the USB-NCM
  link: 8,125 metadata and 87,088 content objects, 3,829,524 KiB in 487 s.
  The host repository needed `ostree refs --create` plus `ostree summary
  --update` and was pulled with `--no-gpg-verify` because it was assembled by
  `ostree pull` and had no `summary`.
- `/ostree/repo/config` had `bootloader=aboot`; it was changed to
  `bootloader=none` (backup `config.aboot`) so a deploy writes BLS entries and
  the kernel into `/boot` instead of regenerating an Android boot image in
  LumaBoot. `ostree admin deploy --os=luma --no-merge <commit>` then succeeded
  in place.
- libostree wrote `ostree-2.conf` (version 2) with
  `ostree=/ostree/boot.0/luma/34b46608.../0`, and
  `/ostree/boot.0/luma/34b46608.../0` resolves to
  `deploy/luma/deploy/d9d453ac....0`. On the next boot the shared resolver
  logged `booting entry 0 of 2` and repointed `root.b` from the fp6 deployment
  to `d9d453ac....0`; prepare-root booted it on the 7.3-rc7 kernel.

### The missing phone user

The generic commit's `/usr/etc/passwd` has only `root`, with `root:*` (locked):
`luma` is created by `scripts/mobile/build-phone-userdata.sh`, which
`ostree admin deploy` does not run. The system booted but had no login.

Bring-up recovery: a throwaway RAM-boot image (`luma-barbet-boot-v5-provision.img`,
SHA256 `319b523a...`) added one `hooks-extra/60-luma-barbet-provision.sh` that
sets a known root password in the deployment selected by the resolver; this
hook is not part of the clean boot image. `boot_b` was left at the clean
`luma-barbet-boot-v4-avb.img` (`b80c7f9c...`). After logging in as root on
ttyMSM0: `useradd -u 1000 -d /var/home/luma -m -k /etc/skel -s /bin/bash luma`,
`chpasswd`, `groupadd -g 1500 luma-display` (the commit has no such group),
and `usermod -aG wheel,audio,luma-display luma`.

### Verified on the generic deployment

- `PRETTY_NAME="Luma (Prairie, Beta 1.1, Nightly 20261110)"`,
  `IMAGE_ID=luma-desktop`; `ostree admin status` shows `d9d453ac....0` as the
  default with origin refspec `d9d453ac...`, and the fp6 deployment
  `97268a77....0` as a GPG-good rollback.
- No `luma-fp6-*` units exist or fail.
- GDM runs a greeter session: `loginctl` shows `c1 ... gdm-greeter seat0
  ... tty1`. `/dev/input` is still empty, so there is no input device yet.
- Failed units: `var-lib-nfs-rpc_pipefs.mount`, `firewalld.service`, and
  `bootloader-update.service` (now masked, as the userdata composer does).
- Disk: `/dev/loop0p2` 107 GiB, 15 GiB used, 88 GiB free.

`systemctl reboot bootloader` remains unsupported, so entering fastboot still
needs the hardware key combination. With a working account, further bring-up
can proceed over SSH on the USB-NCM link (start `sshd` once) or the UART.

## 2026-11-12 GUI on barbet: display, greeter and input keys

### Display

The generic deployment boots to GDM with a live greeter session
(`loginctl`: `c1 ... gdm-greeter seat0 ... tty1`) and `gnome-shell --mode=gdm`.
`/sys/class/drm/card0-DSI-1` is `enabled`/`On` with the backlight on while the
greeter is active. The user log shows only expected software-rendering noise
(freedreno/TU probe failures because there is no GPU, then
`Created gbm renderer for '/dev/dri/card0'`), plus GSD backlight assertions
`gsd_power_backlight_*_abs_to_percentage: assertion 'max > min' failed`.

With no input devices the session goes idle after ~1 minute and mutter blanks
the output (`dpms=Off`, `bl_power=4`); `systemctl restart gdm` re-enables it
for the whole session. That is normal idle blanking, not a modeset failure.

### Keys

Kernel additions: `CONFIG_INPUT_MISC`, `CONFIG_INPUT_PM8941_PWRKEY`,
`CONFIG_INPUT_UINPUT`, `CONFIG_KEYBOARD_GPIO`. DTB: `&pon_pwrkey` was already
enabled; `&pon_resin` is enabled with `linux,code = <KEY_VOLUMEDOWN>`. The
stock DTS shows the volume-up key on **pm8150l** GPIO5 (its `gpio_keys` uses
the pm8150l pinctrl phandle), not pm8150.

Boot image `luma-barbet-boot-v6-input.img` (SHA256 `24192608...`) boots from
slot b and registers `pm8941_pwrkey` (event0) and `pm8941_resin` (event1).
Display and greeter as above. `sshd` is enabled on this deployment for
bring-up (the published images deliberately ship it off: an always-on SSH
server with the default `luma`/`1234` account would be reachable from Wi-Fi
and USB).

### Volume-up attempt and ABL slot fallback

`luma-barbet-boot-v7-keys.img` moved the volume-up key to
`&pm8150l_gpios 5` with a pinmux state. After flashing it, the boot did not
reach SSH before ABL **switched the active slot back to `a`**
(`current-slot: a`, `slot-successful:b: no`, `slot-retry-count:b: 3`), which
booted Android recovery (slot a Android can no longer mount the erased
`userdata`). Rewriting `boot_b` alone did not help: `fastboot set_active b`
was required to make ABL boot slot b again. The v7 failure cause is not yet
known (the UART window after that reboot captured only Android's charger
log), and volume-up is not needed for the greeter: power and volume-down both
work. Slot b is now on v6 and healthy.

Lesson: after any slot-b boot failure, check `fastboot getvar current-slot`
and `set_active b` before assuming the image is bad.

### Session

GDM autologin for `luma` is enabled on this deployment
(`/etc/gdm/custom.conf`, `AutomaticLoginEnable=True`), so the phone boots
straight into a GNOME session: `loginctl` shows `9 1000 luma seat0 ... user
tty2`, `gnome-shell` runs as `luma`, and `card0-DSI-1` is `enabled`/`On`.
That is the first fully started graphical user session on barbet. It is the
generic ARM (desktop) image, so the session is GNOME, not Phosh.

### Touch: what the SPI panel needs

The barbet touch is `st,fts` on `spi@884000` (QUP0 SE1, `qcom,spi-geni`,
10 MHz, `reg = <0>`), IRQ TLMM GPIO9, reset TLMM GPIO8, max coordinates
1079x2399, plus `st,panel_map`, `st,force-pi-cfg-ver-map = <0x300>` and the
`tbn,lpi2ap_gpio` (GPIO5) / `tbn,ap2lpi_gpio` (GPIO35) handshake that moves
the controller from the sensor DSP (SLPI/LPI) to the AP.

Three gaps, in order:

1. `sm7250.dtsi` has no `spi@884000` node; SE1/SE6 are also listed in
   `gpio-reserved-ranges` as TrustZone-owned, so the pins and the handshake
   need to be understood before Linux can talk to the chip.
2. Upstream has no SPI ST FTS driver. The FTS5 series (v8, 2026-09) adds
   `st,stmfts5` support and basic multi-touch, but only as an **i2c_driver**;
   barbet is SPI. The vendor `fts_ts` driver is SPI, so either the stmfts
   transport is extended to SPI or the vendor driver is ported.
3. Power: the stock node has no `avdd`/`vdd` supplies described in our DT
   yet; the vendor tree uses PMIC rails that must be modelled.

So touch is a driver project, not a DT one-liner. The working GUI plus
power/volume-down keys and SSH is the current usable state; volume-up is not
enabled (the v7 attempt did not boot, see above).

### Correction: v7 boots; the fallback was the reboot path

`luma-barbet-boot-v7-keys.img` boots normally. With a forced reboot the UART
shows `Booting from slot (b)`, `Machine model: Google Pixel 5a`, then
`input: pm8941_pwrkey`, `input: pm8941_resin` and
`input: gpio-keys as /devices/platform/gpio-keys/input/input2`, the OSTree
resolver selecting the generic deployment, and the login prompt. Live state:
three key devices (`event0` power, `event1` KEY_VOLUMEDOWN, `event2`
KEY_VOLUMEUP), `card0-DSI-1` enabled/On, and a `luma` seat0 session.

The earlier "v7 failure" was the shutdown, not the image. `systemctl reboot`
on this system hangs long enough for systemd-shutdown to arm `qcom_wdt`;
the log ends with `Failed to set watchdog hardware timeout to 10min: Invalid
argument` and `PM: Reason: watchdog`, and the next boot carries
`androidboot.bootreason=watchdog`. That path left ABL on slot `a` (Android
recovery) until `fastboot set_active b`. Use `systemctl reboot --force` (or
`reboot -f`) for bring-up cycles to avoid the hung shutdown.

The volume-up DTB change is only the `key-vol-up-default` pinmux state and the
key moving from `pm8150_gpios` to `pm8150l_gpios` (stock phandle for the
volume-up key); pm8150l GPIO5 has no other user (panel reset is GPIO8).

### Touch progress: SPI bus and device are up

- `&i2c0` (QUP0 SE0, `i2c@880000` in `sm7250.dtsi`) is now overridden as
  `qcom,geni-spi` in the board DTS, FIFO mode (GPI DMA and the wrapper's
  `iommus` removed because `apps_smmu` does not probe). Pinmux
  `qup00` on TLMM GPIO42–45. Result: `/sys/class/spi_master/spi0` exists.
- `touchscreen@0` with `compatible = "st,fts"`, reset TLMM 8 (active low),
  IRQ TLMM 9 (falling), `lpi2ap` GPIO5, `ap2lpi` GPIO35, 1080x2400.
  Result: `spi0.0` enumerates; no driver is bound yet.
- Boot images used: `luma-barbet-boot-v8-spi.img` (`3b0e87a0…`, SPI master)
  and `luma-barbet-boot-v9-touchdt.img` (`6dad31a8…`, device node).
- Vendor reference for the missing driver:
  `android.googlesource.com/kernel/msm-modules/fts_touch`, branch
  **`android-msm-barbet-4.19-android11-d2`** (this exact phone). The SPI
  read/write framing is in `fts_lib/ftsIO.c`: one `spi_message` with a
  command transfer followed by an rx transfer carrying `SPI_DELAY_CS`;
  reset and the AP/SLPI switch are in `fts_lib/ftsCore.c` (`setResetGpio`,
  `fts_set_switch_gpio`), and the chip-ID constants (`DCHIP_ID_0/1`,
  `ADDR_DCHIP_ID`) are in `fts_lib/ftsHardware.h`. A copy of `fts.c`,
  `ftsIO.c`, `ftsHardware.h` and `ftsCore.c` is in the experiment directory
  under `vendor-fts/`.

Next step: implement `stfts-spi.c` (probe: handshake, reset, chip-ID read)
per the plan's Task 3, deploy as v10, and read the ID from `dmesg`.

## 2026-11-12 Touch: bus, driver and IRQ work; firmware does not report contacts

State at the stop point (boot image `luma-barbet-boot-v20-sense.img`):

- SE0 (`spi@880000`) is a GENI SPI master in FIFO mode; `spi0.0` is the
  `st,fts` panel; `spi_master/spi0` and the input device `stfts_spi` exist.
- `drivers/input/touchscreen/stfts-spi.c` (bring-up driver, not a release
  driver) reads the chip ID `0x3648` (FTS5), sends the FTS5 scan mode
  `{0xA0, 0x00, 0x01}` (BIT(0) multi-touch; `0x00` does not enable sensing),
  registers the IRQ (GPIO9, level-low as the stock DTS specifies) and a
  multitouch input device.
- The controller now **interrupts** (IRQ 110 counts up) and the FIFO reads
  work. Every event it emits is a status event: `43 05 30`, `43 05 11`,
  `43 06 01 5f`, `43 06 04 b5` — force-calibration and water-mode status.
  No `0x10/0x20/0x30` contact events, and the FIFO stays empty while the
  panel is touched.
- Bus ownership is set by `lpi2ap`; AP mode is **high** (upstream FTS5 keeps
  the AP on). Driving it low (earlier attempt) leaves the chip in sensor-DSP
  mode; both produce the same status events, so this is not the blocker.
- `cat /sys/bus/spi/devices/spi0.0/fifo` reads one event and is safe; a
  64-byte read while the FIFO was empty hung the system once (watchdog
  reboot), so reads are now the vendor two-step (one event, then the
  remaining count from byte 7).

The remaining work is the firmware initialization: the vendor driver
(`fts_lib/ftsCore.c`, `ftsTest.c`) reads system info and runs panel
initialization/calibration (`production_test_*`, `readSysInfo`,
`fts_chip_initialization`) that the minimal driver does not. The FW is stuck
in force-calibration, which is consistent with a missing panel map/config.
The next attempt should port that vendor initialization (or the full
`fts_lib`) rather than extend the minimal driver.

Kernel additions used: `CONFIG_INPUT_TOUCHSCREEN` (the whole touchscreen
directory was not linked without it) and `CONFIG_TOUCHSCREEN_STFTS_SPI`.

## 2026-11-12 Touch: contacts confirmed (PM8150 LDO13 / 3.304 V)

Follow-up to the stop point above. The missing piece was not another firmware
command: the touch panel's analog rail was never enabled.

- v21 RAM boot wedged at the first 217-byte framebuffer read. The read
  exceeded the SE FIFO and selected `GENI_SE_DMA` on a FIFO-only controller;
  UART ended at `event 03` and the watchdog reset the board ~368 s later.
- v22 reads the framebuffer in 16-byte chunks and boots. Panel init via
  sysfs was accepted (`43 01 a4 00 02`) but touching still produced only
  status events.
- v23 added the vendor golden-MS-raw save (`0xC0 0x01 0x01`) after panel
  init and added `0x306` to `st,force-pi-cfg-ver-map`, so boot init runs on
  this unit. It still logged `43 05 30` (invalid mutual forced calibration)
  and no contacts.
- Root cause: the stock `lito-barbet-touch.dtsi` sets `&pm8150_l13` to
  3.304 V with `regulator-always-on`. The L13 node was not described in our
  board DTS, so the FTS digital core ran on its 1.8 V I/O domain (SPI, chip
  ID and IRQ all worked) while the analog front end had no valid mutual raw
  data.
- v24 adds `vreg_l13a_3p3: ldo13` in `regulators-0` (3304000 uV, HPM,
  `regulator-boot-on`, `regulator-always-on`). Image
  `luma-barbet-boot-v24-l13.img`, SHA-256
  `8628b0b4e5095e122f4d764e04a6e265adc0b529954f352b70384a9fe29a661c`,
  written to `boot_b` from the running v23 system over USB-NCM, read back
  with a matching SHA-256, then `systemctl reboot --force`.
- Evidence, UART `/tmp/opencode/v24-uart.log`: `vreg_l13a_3p3` set to
  3304 mV, `force pi cfg map 0306 -> panel init`, `panel init complete`,
  `touchscreen ready (irq 110)`. `regulator_summary` reports
  `vreg_l13a_3p3 ... 3304mV`. No `43 05 30` occurs after boot.
- Touch test: dmesg contains 50 contact events. First sequence
  `13 01 de 90 61 37 00 00` (enter, id 0, x 222, y 1561) followed by
  `23 ...` motion events. A `/dev/input/event2` capture
  (`/tmp/opencode/event2-v24.hex`) contains `ABS_MT_TRACKING_ID`,
  `ABS_MT_POSITION_X=338`, `ABS_MT_POSITION_Y=1390`,
  `ABS_MT_PRESSURE=66`, and `BTN_TOUCH=1`, so userspace receives the
  contacts.
- Slot `b` remains active; `androidboot.slot_retry_count=1` is unchanged.
  Only `boot_b` was written; no slot metadata, `misc` or slot-`a` storage
  was touched.

This is still the bring-up driver, not a release driver. Suspend/resume,
firmware update, gesture/grip handling and coordinate calibration across
the full 1080x2400 area remain untested.

## 2026-11-14 repository integration, v161 source baseline

The bring-up tree is now a reviewable repository integration rather than an
external source pile. The authoritative tested baseline is the **v161 / kernel
124** image and its DTB, not the earlier v24/v154 images or the historical
blocked-audio entries above.

### What is in the tree

- Source lock `config/kernel/barbet-7.3.json`: Linux v7.3-rc7 plus the avicii
  SM7250 overlay and the `patches/linux-barbet/7.3` board/driver series.
- `socs/qcom/sm7250/soc.toml`, the strict `devices/google/barbet` manifest,
  boot policy, firmware manifest and tests.
- `scripts/kernel/build-barbet.sh`, `scripts/mobile/build-barbet-boot-image.sh`
  and `scripts/mobile/build-barbet-install.sh`: prepare, build and assemble a
  full test set from the lock.
- `config/mobile/barbet-initramfs`: the boot overlay, ADSP/audio service,
  ACP profile, Wi-Fi service and the post-resume `dwc3` hook.

### Verified on the v161 device

- Display, backlight, touch contacts and the power/volume keys.
- Adreno GPU initialized and used by Phoc/Phosh.
- Speaker: both TAS2562 amps active (`PWR_CTRL=0x0c`), TDM clock `0x44`, and a
  2 kHz tone recorded through the mic at about 143x the no-tone baseline.
- Microphone: RT5514P capture with real samples (nonzero RMS, ~19.7k distinct
  values over five seconds).
- `s2idle` suspend/wake with display/touch restored and a post-resume USB
  rebind.

### Verified on the repo-built kernel (2026-11-14, 22-patch lock)

- Boot from `boot_b` with slot b active; SSH and USB-NCM come up.
- Display: `card0-DSI-1` connected at 1080x2400, panel init complete,
  backlight registered. GPU: `gpu-initialized: 1`, Phoc on `drm:card0`.
- Touch: `stfts_spi` (chip `0x3648`) reaches `/dev/input/event2`.
- Speaker: audible through PipeWire after the barbet card is pinned to S16;
  PipeWire otherwise negotiates S24, starts the TDM backend at the wrong slot
  width and is silent.
- Microphone: not working. Capture returns a constant DC offset with 2-3 LSB
  of noise; the DMIC/TDM capture route still needs work.
- Wi-Fi: the `IPCRTR` (QRTR) channel, the PD mapper, `tqftpserv`, the modem
  with the stock `0x11800000` memory map, and the WCN3990 firmware are all in
  place. The modem still does not fetch `wlanmdsp.mbn`, so `WLFW` never
  registers and ath10k creates no `wlan0`. The FP6 platform services are
  masked and the barbet service owns the start order; the remaining work is
  the modem/WLAN PD bring-up.

### Built but not yet a works claim

- The full set builds from the lock: a 100663296-byte boot image (AVB NONE,
  rollback 1783209600), the shared ARM userdata, `SHA256SUMS` and the exact
  install commands. The full-install hardware gate still has to run on the
  slot-b install.
- Battery and vibrate are PR 2: the PM7250B charger is disabled and there is
  no battery gauge or DRV2624 haptics driver yet.

### Limits

The install writes `userdata` + `boot_b`, sets slot b and erases Android; the
factory image is the recovery path. `modemst1/2`, `fsg`, `fsc`, `persist`,
`dtbo`, `vbmeta`, the bootloader and `super` are preserved. SELinux remains
permissive while the device is `experimental`. No firmware, private captures,
identities or release images are in the repository.
