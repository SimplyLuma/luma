# Pixel 5a (barbet) as a Luma device

Engineering decision for landing Google Pixel 5a support so the nightly phone
pipeline can emit a barbet boot image. This is a bring-up plan with explicit
hardware gates, not a claim that the phone is a supported device yet.

## Target and current evidence

- Device: Google Pixel 5a, codename `barbet`, SKU `G4S1M`, SoC Qualcomm
  SM7250 / Snapdragon 765G (`qcom,sm7250`, `qcom,lito`).
- Verified on hardware today, booting from slot `b` only:
  UFS storage, USB-NCM networking, DSI/DRM modeset, backlight register
  readback (not yet visible brightness changes), GNOME session,
  power/volume-down keys, and the ST FTS SPI
  touchscreen (contacts on `/dev/input/event2`).
- Touch root cause was the missing PM8150 LDO13 (panel AVDD, 3.304 V), fixed
  and deployed as `luma-barbet-boot-v24-l13.img`.
- Not verified: Bluetooth, speaker/headset audio, microphone, modem data/calls,
  cameras, charging control, full-area touch calibration.
- Wi-Fi verified on hardware: WCN3990 associated to `spud_2` (86.7 Mbit/s),
  DHCP `192.168.1.146`, default route, ICMP to 1.1.1.1 and DNS resolution.
  The test needed the full modem range, the QRTR HELLO-gate fixes, a separate
  barbet firmware store, `tqftpserv` and a barbet `board-2.bin`; these must be
  automated in the device layer before this is a reproducible install.
- Suspend/resume: s2idle wakes from the power key and restores display and
  touch. The USB gadget needs a post-resume `dwc3` rebind; a test hook does
  that automatically. Battery drain is unmeasured.
- Brightness: the brightness slider visibly changes the panel. Calibration
  (mapping, minimum level, linearity) is still unverified.
- Every hardware claim in the final PR must point at a captured UART or SSH
  transcript. Code inspection or a clean build is not evidence.

## GPU follow-up (requested)

After radios and audio, verify the Adreno 620 path on barbet: GPU zap shader
firmware, `a650_sqe.fw`/`gmu` firmware, the GPU node and cooling, and a real
freedreno/Turnip render. Do not fold GPU claims into the first radio/audio PR
unless the same evidence gate is met.

## Wi-Fi/Bluetooth bring-up finding (2026-11-12)

The WCN3990 path is not a small DTS/config delta on barbet. The mainline
`qcom_q6v5_pas` modem loader rejects the stock Pixel 5a modem image:

- `modem.mdt` PT_LOAD segments extend slightly past the stock
  `modem_wlan_mem` reservation (`0x8c000000` + `0xf800000`), and a trailing
  zero-file-size BSS segment extends much further. Skipping zero-file-size
  segments gets past the first error.
- The image also sets the relocatable flag while its PT_LOAD addresses are
  absolute. Honouring that flag asks the secure world for a range larger than
  the reservation and fails.
- Enlarging `modem_wlan_mem` over the unused camera carveout and starting the
  modem hard-reset the phone. That is not an acceptable bring-up fix.

Bluetooth additionally requests `qca/crbtfw32.tlv` and `qca/crnv32.bin` from a
built-in `hci_qca` probe before the root filesystem (and the writable firmware
store) is mounted, so it must be deferred or given initramfs firmware. ADSP
auto-boot has the same pre-rootfs firmware problem and is disabled in the test
image.

Conclusion: Wi-Fi, Bluetooth and audio need a proper SM7250/WCN3990 platform
integration (correct modem memory map/PAS setup, firmware delivery before
rootfs where required, and a barbet audio machine driver). They are blocked
for the first PR, not merely untested.

The upstream QRTR name-service fixes (hold control packets behind the HELLO
gate; keep announcing after one failed send) are required for the modem to
learn about rmtfs/tqftpserv and avoid its watchdog. They are carried as
reviewed overlay patches with the upstream commit message and author. The
modem also needs the full 0x11800000 modem range and a separate
`/var/lib/luma/device-firmware-barbet` store; the FP6 store must stay
manifest-clean.

Wi-Fi is verified with that stack: `wlan0` scans, associates to `spud_2` at
86.7 Mbit/s, gets DHCP, and passes ICMP/DNS. Automating `tqftpserv`, the
firmware path and the board-2 container is still device-layer work.

## Audio and Bluetooth status (2026-11-12)

- **CS35L41:** both amps probe on I2C once `VP` is the battery/boost rail
  (`vph_pwr`) and `VA` is the 1.8 V system rail. `boot_b` v38 has the nodes,
  resets and pinctrl.
- **ADSP:** starts with `adsp.mdt`, but its sensor process crashes after about
  ten seconds (`sensor_process ... sar.cc:27 chre_utils fatal`). It needs the
  FastRPC file server (`hexagonrpcd`) and the phone's sensor registry, neither
  of which exists in the current userspace image. The sound card therefore
  remains deferred and no PCM is created.
- **Bluetooth:** enabling UART5 with the RX-wake IRQ removed and QCA firmware
  in the initramfs still wedges the built-in `hci_qca` probe before userspace
  and watchdog-resets the phone. It needs a deferred/module load or more
  WCN3990 power-sequence work.
- **GPU:** the display works, but DPU logs `no GPU device was found`; the
  Adreno node is not enabled and the A650 zap/GPU firmware path has not been
  brought up. GNOME is not evidence of hardware rendering.

Audio is blocked on an ADSP/FastRPC userspace component, not a small kernel
change. The first PR can only claim the verified Wi-Fi/touch/display/suspend
subset; Bluetooth, audio and GPU remain follow-ups.

## Delivery path: source lock (option A)

The closer phone precedent is [OnePlus 6T PR #289](https://github.com/SimplyLuma/luma/pull/289),
reviewed as an open proposal, not a merged or approved contract. Its diff adds
an experimental device/SoC, source lock, firmware inventory, installation
guide and device tests without inventing a kernel tag, published release or
separate OS update ref. Barbet follows the shared `luma/1/aarch64/nightly`
ref too. Registration alone does not establish automated nightly boot-image
production; verify that separately. Unlike fajita, barbet needs header v2,
its own board patches and the stricter slot-b-only deployment procedure.

There is no `barbet`/`sm7250` branch or tag in `SimplyLuma/linux`, so barbet
cannot use the Fairphone published-tag path yet. Follow the Surface Pro 12
pattern instead:

- `devices/google/barbet/device.toml` uses `[kernel] source_lock`, not
  `[kernel] tag`.
- `config/kernel/barbet-7.3.json` pins:
  - `sources.linux`: the exact v7.3-rc7 tree (GitHub tag archive or
    kernel.org tarball; hash verified at lock time);
  - `sources.overlay`: the pinned `lazybrar/avicii-mainline` snapshot
    (`780934bbcc758694a9e4e8950eed9c040725df4d`), which supplies the SM7250
    base patch set and its `patches/COMMITS.txt` provenance;
  - `patches`: the avicii combined patches (`source = "overlay"`) followed by
    the barbet patches committed under `patches/linux-barbet/7.3/`
    (`source = "repository"`).
- `config/kernel/barbet.config` is a complete, committed arm64 config (the
  Surface Pro 12 convention) so the build is deterministic.
- `scripts/kernel/prepare-source.py` already enforces the lock. A new
  `scripts/kernel/build-barbet.sh` builds the image and records the source
  lock receipt exactly as the FP6 builder does.

This keeps provenance reviewable, needs no push access outside this repo, and
allows the same patch series to become a `sm7250` kernel tag later.

## Repository layout

Following `devices/fairphone/fp6` and `devices/microsoft/sp12in`:

```
socs/qcom/sm7250/soc.toml
devices/google/barbet/
  device.toml
  boot.toml
  firmware.manifest
  INSTALL.md
  tests/test_google_barbet.py
config/kernel/barbet-7.3.json
config/kernel/barbet.config
patches/linux-barbet/7.3/
  ...barbet board and driver patches...
scripts/kernel/build-barbet.sh
scripts/mobile/build-barbet-boot-image.sh
```

`tools/check-devices.py` requires exactly those five entries in the device
directory, at least one test, a CODEOWNERS entry that lists the manifest's
maintainers, and a firmware manifest whose count matches. The code must pass:

```sh
python3 -B tools/check-devices.py
python3 -B -m unittest devices.google.barbet.tests.test_google_barbet
```

## Kernel patch series

The avicii base already provides SM7250 GCC/RPMh/interconnect/pinctrl, UFS,
USB, display/DPU, GPU, MPSS/WCN3990, ADSP/APR, and WSA/MI2S plumbing. Barbet
adds only board and device differences:

1. **Board DTS** (`0001-arm64-dts-qcom-add-google-barbet.patch`)
   - `sm7250-google-barbet.dts` + `Makefile`/`dtbs-list` entry.
   - Pixel carveouts, RMTFS, ramoops, UART2 console, UFS/USB, PMIC rails
     (including LDO13), keys, TLMM reservations.
   - Display: MDSS/DPU/DSI, PON restart reason, sofef01 panel node on DSI0,
     `vreg_l16a_3p0` (panel VCI), `vreg_l1c_1p8` (VDDIO).
2. **Touch driver** (`0002-Input-add-ST-FTS-SPI-touchscreen.patch`)
   - Complete `drivers/input/touchscreen/stfts-spi.c`, Kconfig/Makefile, and
     `Documentation/devicetree/bindings/input/touchscreen/st,fts-spi.yaml`.
   - Features: optional `vdd` supply, reset/lpi2ap/ap2lpi GPIOs, chip-ID
     handshake, SYS_INFO parse, config-version panel init, golden MS raw save,
     multitouch input device, sysfs diagnostics (`fifo`, `sysinfo`, `init`),
     managed resources, and system PM suspend/resume that resets and
     re-initializes the controller before unmasking the IRQ.
   - Explicitly out of scope for the first PR: firmware update, gestures/grip,
     and full-area coordinate calibration (documented in the binding and
     INSTALL.md).
3. **Panel driver** (`0003-drm-panel-add-samsung-sofef01.patch`)
   - New `panel-samsung-sofef01.c` derived from the stock panel node:
     1080x2400 at 60 Hz, 4 lanes, command mode, exact on/off/sleep
     sequences, DCS brightness 0..0x3ff, 68x151 mm.
   - Do not keep the bring-up hack that overloaded `samsung,sofef00` with a
     1080x2400 mode; the two panels have different init sequences.
4. **Wi-Fi and Bluetooth** (`0004-arm64-dts-qcom-barbet-enable-wcn3990.patch`
   plus any required QRTR fix)
   - `&wifi` with the board supplies (0.75/1.8/1.3/3.3 V), WLAN firmware name,
     and reserved memory.
   - `&uart5` (QUP1 SE5, 0x894000, TLMM 38-41) with `qcom,wcn3990-bt`,
     `max-speed`, and the board supplies; TLMM 73 is `qca,bt-sw-ctrl-gpio`.
   - The avicii project needed a qrtr name-service retry workaround for
     WCN3990; if barbet needs it, it is carried as a clearly marked `HACK`
     patch with the upstream discussion linked, not silently.
5. **Audio** (`0005-ASoC-...-barbet-speaker.patch` + DTS)
   - First target: speaker playback through the two CS35L41 amplifiers on the
     external TDM link, following the stock `external-playback-dai-link`.
   - Second target: WCD938x SoundWire headset/microphone capture. This is a
     separate gate because the upstream sm8250 machine/SoundWire stack has to
     be adapted to SM7250 addresses and clocking.
   - Do not claim "audio works" from an enumerated PCM or a card that opens;
     playback must be captured as audible output and record/capture separately.

## Device manifest and boot policy

- `soc = "qcom/sm7250"`, `form_factor = "phone"`, `year = 2021`,
  `tier = "experimental"` until the full gate below passes.
- `kernel.release` = `7.3.0-rc7-barbet1` (or the exact `make kernelrelease`
  value), `kernel.dtb = "qcom/sm7250-google-barbet.dtb"`.
- `boot.toml`: `android-boot-v2`, 4096-byte pages, `partition = "boot"`,
  `partition_size = 100663296`, AVB footer `hash` + algorithm `NONE`, rollback
  index `1783209600`, `vbmeta = "untouched"`.
- `dtbo.policy = "none"`: the bring-up constraint forbids writing `dtbo`.
  The complete DTB lives in the boot image. The device kit therefore ships
  only the boot image (Pixel 9 style), with no `dtbo-blank.img`.
- Current deployment writes only `boot_b`, from the running system over
  USB-NCM, with readback and SHA-256 verification before reboot. Do not
  generate generic fastboot-flash kit instructions that bypass this rule.
  Slot-a storage, userdata, `dtbo`, `misc`, `modemst*`, `fsg`, `fsc`,
  `persist`, partition tables and bootloader writes remain forbidden.
- `[install] preserve = ["modemst1","modemst2","fsg","fsc","persist","misc"]`.

## Firmware

No firmware is committed. The phone already has signed WCN3990, ADSP and
WCD938x/CS35L41 blobs in its vendor/modem partitions. The plan:

1. Extract the exact set the mainline drivers request from the operator's own
   `vendor.img`/`modem.img` into an untracked work store.
2. Record name, size and SHA-256 in `firmware.manifest` (never the payload).
3. Firmware is delivered out of band from the operator's own extraction.
   No signed device repo is in scope, and the nightly boot image alone
   must not be assumed to carry firmware.
4. `INSTALL.md` states which functions need the firmware store and how to
   verify the store before claiming Wi-Fi/BT/audio.

Like PR #289, inventory the userspace device layer explicitly: firmware
lookup/module paths, remoteproc services (`rmtfs`, `tqftpserv` if required),
Bluetooth identity, audio UCM/routing, clock initialization and boot-success
handling. Confirm barbet's actual dependencies rather than copying SDM845
services or enabling slot-marking writes. Any missing shared integration is
a named prerequisite/follow-up, not hidden manual state behind a works claim.

## Nightly output

A nightly needs three things from this repo:

1. the device manifest (`devices/google/barbet/device.toml`);
2. a boot-image builder (`scripts/mobile/build-barbet-boot-image.sh`) that
   packs the built `Image`, `qcom/sm7250-google-barbet.dtb`, the shared Luma
   initramfs and the `pmos_root_uuid`/`pmos_boot_uuid` command line, with the
   Android boot header offsets from `socs/qcom/sm7250/soc.toml`;
3. a `[kit]` section so `scripts/devices/build-device-kit.py` can produce
   `luma-barbet-<version>-boot.img` and `FLASH.txt`.

The builder must fail, not silently truncate, when kernel + DTB + ramdisk
exceed `partition_size = 100663296`. The current bring-up image is exactly
that size, so this is a real risk and is checked in the builder test.

## Hardware acceptance gate before the first PR

Each line needs a captured transcript in `docs/pixel5a-bringup-audit.md` and a
matching test or documented manual step:

| Area | Pass condition |
|---|---|
| Boot | Slot `b` boots; `boot_b` read back and SHA-256 matches; slot metadata and `misc` untouched. |
| Storage | UFS LUNs and root filesystem mount read-write on the test deployment only. |
| Display | `card0-DSI-1` enabled at 1080x2400; backlight changes are visible; panel survives blank/unblank. |
| Touch | Contacts reach `/dev/input/event*`; coordinates span the panel; suspend/resume keeps the IRQ alive. |
| Wi-Fi | `wlan0` associates with a real AP and passes traffic; firmware hashes recorded. |
| Bluetooth | `hci0` opens, scans, pairs and transfers a file with a real peer. |
| Audio | Speaker playback is audible and captured; headset/mic capture is deferred. |
| Keys | Power and volume-down generate input events. |
| Suspend (bounded trial) | Repeated suspend/wake restores display, touch, USB-NCM, Wi-Fi/BT and speaker playback; document blockers if substantial work is required. |

Until those pass, `tier = "experimental"` and `INSTALL.md` lists every
unverified function. Modem, cameras, fingerprint, NFC, charging control and
USB host/DisplayPort stay out of scope for the first PR.

## Risks

- **Wi-Fi QRTR**: the avicii set hides a WCN3990 name-service workaround. If
  barbet needs it, Wi-Fi may associate but fail service discovery without it.
- **Firmware provenance**: blobs are device-signed and not redistributable
  from this repo; the nightly pipeline must not pretend it can build them.
- **Boot partition size**: a monolithic kernel plus the shared initramfs must
  fit 96 MiB. Modules may be required; that changes the device-repo design.
- **Audio complexity**: WCD938x + CS35L41 is a full board audio bring-up, not
  a one-line DTS change. Speaker-only via CS35L41/TDM is the reduced first
  gate; capture/calls are later.
- **Slot fallback**: a failed slot-`b` boot can make ABL fall back to slot `a`.
  Re-verify `fastboot getvar current-slot` after every failed cycle. The
  previously authorized fallback recovery is `fastboot set_active b`;
  it changes slot metadata, so do not describe recovery as leaving `misc`
  untouched. Never perform a direct `misc` write. Use
  `systemctl reboot --force` for normal deployment reboots.

## Recorded decisions and remaining release gate

1. **Owner**: leave unowned for now. Keep the device manifest and CODEOWNERS
   additions out until a real maintainer is confirmed; do not insert a
   placeholder or weaken the checker. Device registration and nightly/PR
   readiness remain blocked on this decision. Stage drafts outside `devices/`.
2. **Firmware delivery**: boot-image-only with firmware out of band.
3. **Audio first gate**: speaker playback only; headset and microphone deferred.
4. **Suspend**: include a bounded trial because battery life matters. The
   existing config enables suspend and PSCI idle domains, but the pinned
   avicii base says suspend-to-RAM is untested and its s2idle work is outside
   the pinned patches. Start with supported `/sys/power/mem_sleep` modes;
   do not force unsupported deep sleep. Include small device-PM fixes, but
   stop and report if platform/firmware rework is needed. Successful wake
   is not evidence of lower battery drain; measure power separately.
