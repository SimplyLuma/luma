# Pixel 5a (barbet) Luma integration design

Date: 2026-11-14
Status: design for approval

## Intent

Pixel 5a bring-up exists today as an unpacked kernel tree, a flattened
device tree, phone-local service files and a pile of scripts under
`/home/bronson/luma-barbet-work/`. None of it is in the Luma fork.

The intent is to make barbet a real, fully installable experimental Luma
phone in this repository, in two stacked pull requests:

- **PR 1** imports the verified bring-up: display, touch, brightness, keys,
  GPU, speaker, microphone, suspend/s2idle, Wi-Fi automation, a reproducible
  source lock, builders, the strict device manifest, an install guide, a
  status matrix and tests.
- **PR 2** adds battery and vibrate as working hardware.

Both PRs keep tier `experimental` and install the device for real (not RAM
boot). This work is for review and merge; the Luma maintainers run nightly
builds after merge. The PR therefore supplies the source lock, config,
patches, builders, manifests and tests they need, and publishes no firmware,
images or release/signing infrastructure.

## Goals

1. A clean, linear commit series in the fork that a maintainer can review.
2. Full install to the device: `userdata` plus a boot slot, with the
   phone's identity/calibration partitions preserved.
3. A registered `devices/google/barbet` and `socs/qcom/sm7250` that pass
   `tools/check-devices.py`, with `@Gibbz` as maintainer.
4. Kernel source reproducible from the documented lock: Linux `v7.3-rc7`
   plus the avicii SM7250 overlay, then repository patches.
5. One canonical board device tree, not a flattened DTB or an include
   chain of experiments.
6. A boot-image builder that fails closed and never flashes.
7. Nightly-ready inputs: a strict device manifest, source lock, builder and
   tests so the maintainers can wire nightly after merge. No nightly or
   release publication is performed by this PR.
8. A single full device build for testing: kernel, DTB, boot image and an
   install set paired with the shared ARM userdata, with SHA-256 sums and
   the exact flashing commands. It builds from the lock in a clean cache,
   not from the external bring-up tree.
9. A status document that states what works, what does not, and the
   evidence for each claim.
10. PR 2 adds charger/fuel-gauge and DRV2624 haptics with real hardware
    evidence, or reports a concrete blocker instead of guessing.

## Non-goals

- Device-specific signed RPM repository, published release images or
  signing infrastructure. The PR stops at merge: the maintainers wire the
  nightly afterwards from the committed manifests and builders.
- A barbet-specific published nightly image or update ref.
- Calls, SMS, mobile data, modem service, IMS/eSIM.
- Cameras, fingerprint, NFC, Bluetooth, USB host/DisplayPort.
- Charging current/voltage control beyond charge-enable; thermal tuning.
- Upstream kernel submission or a published kernel tag.
- Any tier above `experimental`.

## Anti-goals

- Calling an area “working” because a PCM opened, a driver probed, or a
  build succeeded.
- Shipping dead experiments (CS35L41 paths, MI2S variants, flattened DTBs).
- Committing firmware, calibration blobs, credentials, raw private
  captures, IMEI/ICCID-shaped data or generated images.
- Hiding a blocker behind an install image.
- Leaving the repository checks red and calling the PR ready.

## Constraints

- Source lock: `org.projectluma.kernel-source/v1`; exactly
  `sources.linux` and `sources.overlay`. The local kernel.org
  `linux-7.3-rc7.tar.gz` is truncated; re-download and hash the pinned
  archives at lock time.
- Repository patches apply after the overlay patches, in numeric order.
- Boot policy: Android boot v2, 4096-byte pages, partition `boot_a`
  (`100663296` bytes), AVB hash footer `NONE`, rollback `1783209600`,
  `vbmeta` untouched, `dtbo` untouched. `fastboot boot` of a custom image
  is rejected on this phone; install is a flash.
- Full install writes `userdata` and `boot_a`, then sets slot `a` active.
  It never writes `modemst1/2`, `fsg`, `fsc`, `persist`, `dtbo`, `vbmeta`,
  the bootloader, `super` or slot metadata beyond selecting `a`. Android
  and its userdata are erased; the factory image is the recovery path.
- Firmware is out of band in `/var/lib/luma/device-firmware-barbet`;
  `firmware.manifest` records names, modes and SHA-256 only.
- `AGENTS.md`: preserve unrelated work and upstream notices; no
  credentials or private evidence; no human sign-off on someone else's
  behalf; report actual checks, never infer success.
- The authoritative tested baseline is the **v161 / kernel #124** image
  and its DTB, not the older v154 image or the historical SDD ledger.

## Current state (verified)

Repository:

- Fork `Gibbz/luma`, currently detached at `3789800f`; only untracked
  design/plan docs for barbet exist. No barbet device, SM7250 family,
  patches, config or builders.
- Strict devices require exactly `device.toml`, `boot.toml`,
  `firmware.manifest`, `INSTALL.md`, `tests/` (+ optional `SUPPORT.md`),
  an owner in `.github/CODEOWNERS` and matching schemas. `maintainers`
  requires at least one entry.

Hardware, current boot (v161, kernel `#124`):

- Display/DSI, backlight, touch (`stfts` event node), power/volume keys.
- Adreno GPU initialized (`gpu-initialized: 1`); Phoc/Phosh use the render
  node.
- Speaker and microphone appear as normal PipeWire sink/source; a 2 kHz
  tone recorded through the mic is ~143x the baseline amplitude; both
  TAS2562 amps report active with `TDM_DET=0x44`.
- `s2idle` advertised; a bounded trial wakes by power key and restores
  display/touch; USB-NCM needs a post-resume `dwc3` rebind hook.
- Wi-Fi was verified earlier (WCN3990 associated, DHCP, ICMP/DNS) but
  `wlan0` is absent now; it needs the modem/`tqftpserv`/firmware-store
  setup automated.
- Battery has no `power_supply` device. Haptics has no driver node.
- `qcom_pm7250b_charger` exists in v7.3-rc7 and the avicii `09-power`
  patch but is disabled and has no DT node. It exposes charger status and
  charge-enable only; there is no fuel gauge.
- Stock haptics is the TI DRV2624 at I2C `0x5a`; the PM7250B LDO vibrator
  is stock-disabled. Mainline has no DRV2624 driver.
- The old SDD ledger (CS35L41 blocked, GPU absent, no audio) is historical
  and must not be used as the current status.

## Approaches considered

**A. One experimental PR with every commit, including battery/haptics.**
Simplest review surface, but the new drivers start unproven and can delay
or destabilise the verified work. Rejected.

**B. Two stacked PRs on one branch (chosen).**
PR 1 is the verified bring-up and can land on its own. PR 2 is a small,
focused battery/haptics change that updates the status matrix. Every commit
is still granular and independently revertable, and both PRs leave the work
in the repository on one stack.

**C. Import only the source lock and document battery/haptics as missing.**
Rejected: the user explicitly wants them brought up.

## PR 1 — verified bring-up and full install

### Commit series

1. `socs/qcom/sm7250: add the SM7250 family`
2. `config/kernel: add the barbet 7.3 source lock and kernel config`
3. `scripts/kernel: add the barbet kernel builder`
4. `patches/linux-barbet/7.3: add the barbet board device tree`
5. `patches/linux-barbet/7.3: add ST FTS SPI touch`
6. `patches/linux-barbet/7.3: add the SOFEF01 panel`
7. `patches/linux-barbet/7.3: enable the Adreno 620 and GPU SMMU`
8. `patches/linux-barbet/7.3: add WCN3990, QRTR and modem memory`
9. `patches/linux-barbet/7.3: add the barbet audio and microphone path`
10. `config/mobile/barbet: add the boot initramfs and platform hooks`
11. `scripts/mobile: add the barbet boot-image builder`
12. `devices/google/barbet: add the experimental device`
13. `docs: add the Pixel 5a support matrix`

The exact split can change if a patch has cross-subsystem dependencies; the
rule is one logical change per commit and no commit that intentionally
leaves the tree unbuildable.

### Board device tree

The tested v161 DTB was produced from an external include chain, while the
repo board DTS is a minimal bring-up file. Reconstruct one canonical
`arch/arm64/boot/dts/qcom/sm7250-google-barbet.dts`:

- keep board differences in the board file and shared SM7250 changes in
  `sm7250.dtsi` (or the overlay patch that owns them);
- include display, touch, keys, UFS/USB, PMIC rails/regulators, GPU/SMMU,
  audio (TAS2562 speaker, RT5514P mic), Wi-Fi/modem memory and the UART
  console fix;
- exclude dead CS35L41/MI2S experiments and anything not used by v161;
- compile and compare against the v161 DTB; any difference must be
  explained and re-validated on hardware.

The board DTS and driver delta must be reconstructed from the exact
`v7.3-rc7` + avicii `780934bb` base; the v161 tree and DTB are the
acceptance reference.

### Platform delivery (Fold-style)

The shared ARM nightly userdata carries no barbet packages, and this design
does not add a signed device repository. The device platform therefore
travels in the boot image's initramfs, like the Pixel 9 Pro Fold:

- `config/mobile/barbet-initramfs/` holds the platform files and a boot
  hook that installs/starts them in the live rootfs.
- Files: ACP card profile (stereo speaker, mono mic), udev rule, ADSP
  firmware staging and start service with the verified mixer routes, Wi-Fi
  automation (modem/WLAN start, `tqftpserv` unit, board-2 alias), and the
  post-resume `dwc3` rebind hook.
- `tqftpserv` is a pinned Fedora RPM plus a unit, not a firmware payload.
- Firmware stays in `/var/lib/luma/device-firmware-barbet`; the install
  guide extracts it from the operator's own factory image. Nothing is
  committed or redistributed.
- The initramfs manifest records every file's SHA-256 and origin, like
  `config/mobile/comet-initramfs.manifest`.

If the boot image's 96 MiB cannot hold the platform files plus kernel,
DTB and ramdisk, stop and report; do not silently drop a service.

### Full build and install set

`scripts/mobile/build-barbet-install.sh` is the one-command test build:

- runs `scripts/kernel/build-barbet.sh` and
  `scripts/mobile/build-barbet-boot-image.sh`;
- takes the shared ARM userdata image (an explicit path or a pinned
  release) and verifies its SHA-256;
- writes `boot.img`, the userdata image reference/copy, `SHA256SUMS` and
  `INSTALL.txt` containing the exact `fastboot flash userdata` /
  `fastboot flash boot_a` / `--set-active=a` commands and the preserved
  partitions;
- refuses a mismatched device, an oversized boot image or an unverified
  userdata image, and never flashes anything itself.

This is the artifact set the hardware validation uses. The OS userdata
image is the shared ARM/nightly image, not rebuilt by this PR.

### Device manifest and firmware

`devices/google/barbet`:

- `device.toml`: `soc = "qcom/sm7250"`, `tier = "experimental"`,
  `form_factor = "phone"`, `year = 2021`, `maintainers = ["@Gibbz"]`,
  `source_lock`, `config`, DTB, boot policy, install method,
  `[initramfs]` and `[updates] ostree_ref = "luma/1/aarch64/nightly"` for
  the maintainers to point the nightly at after merge. The patch series
  lives in the source lock, not the manifest.
- `boot.toml`: `partition = "boot_a"`, v2/4096/100663296/NONE/1783209600,
  `vbmeta = untouched`, `dtbo policy = none`, explicit cmdline and
  generated UUID/ostree entries.
- `firmware.manifest`: generated from the operator's store; includes ADSP,
  GPU (a650/zap), WCN3990, RT5514P DSP and board-2; hashes only.
- `INSTALL.md`: full install (`fastboot flash userdata`,
  `fastboot flash boot_a`, `fastboot --set-active=a`, `fastboot reboot`),
  preserved partitions, recovery, firmware extraction.
- `SUPPORT.md`: the user-facing status matrix.
- `tests/test_google_barbet.py`: lock/patch order, config, boot policy,
  install commands/partitions, firmware manifest, initramfs manifest and
  no-leak checks.

`<socs/qcom/sm7250/soc.toml>`:

- `repository = "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git"`,
  `branch = "master"`, `dts_directory = "arch/arm64/boot/dts/qcom"`.
- The device source lock pins the exact `v7.3-rc7` commit and avicii
  overlay; no new SimplyLuma kernel branch or tag is created.
- Boot offsets: kernel `0x00008000`, ramdisk `0x10000000`,
  `second 0`, tags `0x00000100`, DTB `0x18000000`, base `0`.

### Status matrix

`devices/google/barbet/SUPPORT.md` is the per-area matrix: works / partial
/ not working / follow-up, evidence and limits. `docs/pixel5a-bringup-audit.md`
keeps the detailed, sanitized narrative. PR 1 marks battery and vibrate as
not working yet and states Wi-Fi's real state and remaining prerequisites.

### Validation and evidence

- Build from the lock in a fresh cache; record the source receipt, kernel
  release, `Image` and DTB hashes.
- Full install on the device; verify the partition list actually written and
  the preserved partitions, then boot and record evidence.
- Display: DRM mode, backlight changes; touch: real evdev contacts.
- GPU: debugfs `gpu-initialized`, render-node client.
- Audio: sink `RUNNING`, recorded acoustic tone, mic capture; both amps
  active.
- Suspend: `s2idle` cycle, wake source, display/touch/USB after resume.
- Wi-Fi: association, DHCP, ICMP and DNS recorded, or an explicit blocker.
  The enlarged modem/WLAN reservation previously hard-reset the phone; if
  that recurs, stop and report rather than tuning it further.
- Checks: `tools/check-devices.py`, unit/device tests, shell syntax, patch
  apply/build, boot-image round-trip, manifest hashes.

## PR 2 — battery and haptics

### Battery

Target: battery and charger visible in `/sys/class/power_supply`, with
charge status and capacity, and charge-enable behaviour.

- Enable `CONFIG_CHARGER_QCOM_PM7250B` (driver already in v7.3-rc7 and the
  avicii `09-power` overlay).
- Add the PM7250B SMB5 node (`qcom,pm7250b-charger`, reg `0x1000`) with the
  USB/DC interrupts, `power-supplies` pointing at the gauge, and the board
  supplies the driver needs.
- Fuel gauge: the stock `google,bms` has no mainline driver. Evaluate a
  minimal gauge using the PM7250B ADC/charger status; if capacity cannot
  be derived safely from documented registers, PR 2 exposes charger status
  and reports capacity as blocked rather than inventing a number.
- Validation: plug/unplug, charge/discharge direction, percent movement,
  suspend interaction, no writes outside the charge-enable bit.

### Haptics

Target: felt vibration through the input force-feedback API.

- New minimal `drv2624` I2C input driver with reset GPIO, `ti,*` tuning
  properties from the stock node, and FF rumble/periodic effects.
- DT node at I2C `0x5a`, reset on PM8150 GPIO1, pinctrl default.
- `CONFIG_INPUT_DRV2624` enabled.
- Validation: `fftest`/`evtest` effect plus a human-confirmed felt
  vibration; no continuous buzzing, no suspend regression.

### PR 2 commits

1. `power: supply: add the PM7250B charger` (enablement + DT)
2. `power: supply: add barbet battery capacity` (only if the gauge is
   implemented)
3. `input: add the TI DRV2624 haptics driver`
4. `arm64: dts: barbet: add charger and haptics`
5. `devices/google/barbet: update the support matrix`
6. `config/kernel: enable the new drivers`

PR 2 updates `SUPPORT.md` only after the hardware evidence exists. A
blocked half is reported in the PR description, not hidden.

## Decisions made

- Two stacked PRs (user choice), one branch.
- Strict `experimental` device, `@Gibbz` as maintainer, full install.
  Nightly wiring is left to the maintainers after merge.
- Install writes `userdata` + `boot_a`, sets slot `a`; the phone's
  identity/calibration partitions and `vbmeta`/`dtbo` are preserved.
- Platform files travel in the boot initramfs (Fold-style); no signed
  device repository.
- Wi-Fi is in PR 1, including the modem/WLAN reservation, QRTR patches,
  `tqftpserv` and the board-2 alias, with a stop-and-report gate.
- Status matrix lives in `devices/google/barbet/SUPPORT.md`; narrative
  stays in `docs/pixel5a-bringup-audit.md`.
- PR 1 excludes dead CS35L41/MI2S experiments; PR 2 owns charger/haptics.
- Tier stays `experimental`; SELinux may stay permissive while the port is
  experimental (the matrix says so), and enforcing is a later gate.
- Battery target is status + capacity; if the gauge proves unsafe,
  status-only with a reported blocker.

## Left to the builder

- Exact patch boundaries, commit messages and test contents.
- The canonical board DTS reconstruction and any SoC/board split.
- The initramfs platform file layout and boot hook.
- `firmware.manifest` generation from the operator's store.
- The battery gauge feasibility and the exact DRV2624 register/FF design.
- Evidence collection and the final status wording.

## Defaults (owner may override)

1. **Battery acceptance:** target charge status + percentage; if the gauge
   proves unsafe, keep status-only and report capacity as blocked.
2. **Wi-Fi fallback:** if the modem/WLAN reservation or `tqftpserv` cannot
   be made reproducible in PR 1, stop and report; do not claim Wi-Fi.
3. **SELinux:** permissive while `experimental`; enforcing is a later gate.
