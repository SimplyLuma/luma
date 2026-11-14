# Pixel 5a (barbet) Luma Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the Google Pixel 5a (barbet) as a strict `experimental` Luma device with a full install, in two stacked pull requests: verified bring-up, then battery and haptics.

**Architecture:** Pin Linux v7.3-rc7 plus the avicii SM7250 overlay, carry board/driver changes as repository patches, build with clang/LLD from a committed config, and install the shared ARM userdata plus a barbet boot image. Platform services travel in the boot initramfs, Fold-style, so the maintainers can wire nightly after merge without a signed device repository.

**Tech Stack:** Linux C/DTS, ASoC, arm64 clang/LLD, Android boot v2, TOML/JSON, Python 3 unittest, fastboot, USB-NCM/SSH.

**Spec:** `docs/superpowers/specs/2026-11-14-pixel5a-integration-design.md`

## Global Constraints

- Source lock: `org.projectluma.kernel-source/v1`; exactly `sources.linux` and `sources.overlay`; repository patches after overlay patches in numeric order.
- Base: Linux `24a19b953e2a3d0ea5a8c4951a0ab8f32720c907` (`v7.3-rc7`); overlay `780934bbcc758694a9e4e8950eed9c040725df4d`.
- Authoritative tested baseline: image `v161-speaker-volume.img` (SHA-256 `9c64293f54567e319caeaf945692fb8aade2df12c4bda906320b99ee0f98b994`), kernel `#124`, DTB SHA-256 `761d489dfff689aa6f665af2a8a47c4f77f23d0da487cc3fc2a5fa5947368234`.
- Boot policy: Android boot v2, 4096-byte pages, `boot_a`, `100663296` bytes, AVB `NONE`, rollback `1783209600`, `vbmeta` untouched, `dtbo` untouched.
- Full install writes `userdata` + `boot_a`, sets slot `a`. Never write `modemst1/2`, `fsg`, `fsc`, `persist`, `dtbo`, `vbmeta`, bootloader, `super` or slot metadata beyond selecting `a`.
- Firmware stays out of band in `/var/lib/luma/device-firmware-barbet`; manifest records names, modes and SHA-256 only.
- Tier `experimental`; maintainer `@Gibbz`; SELinux may be permissive until a later gate.
- No device-specific signed repo, release image, nightly publication or signing in this work; the maintainers wire nightly after merge.
- Every hardware claim needs a captured transcript or real sample; a probe, PCM open or service start is not a works claim.
- Preserve unrelated work, upstream notices, licenses and the untracked docs already in the worktree. No secrets, IMEI/ICCID data, firmware blobs or generated images in Git.

## Review Focus

1. **Preserved partitions on a full install.** A typo in the install guide or `[install] preserve` must not erase `modemst*`, `fsg`, `fsc`, `persist`, `dtbo` or `vbmeta`. Test: device test parses INSTALL.md and asserts every forbidden flash/erase is absent.
2. **Source-lock integrity.** A stale archive, mismatched patch hash or wrong patch order must fail before any build output is published. Test: negative cases for bad lock, bad patch hash and non-empty output.
3. **Boot-image failure is closed.** Oversize, missing DTB/ramdisk, wrong header or failed AVB must not replace an existing output. Test: fake-tool cases and a real round-trip.
4. **Audio claims are acoustic.** Both TAS2562 amps active and a recorded tone, not merely a `RUNNING` PCM. Test: the hardware checklist in Task 16 requires the recorded 2 kHz ratio and live amp registers.
5. **Battery/haptics do not regress suspend or identity.** No writes outside the charge-enable bit; suspend still wakes; haptics does not block suspend. Test: PR 2 hardware checklist.

---

### Task 1: Branch, SM7250 family and ownership

**Files:**
- Create: `socs/qcom/sm7250/soc.toml`
- Modify: `.github/CODEOWNERS`
- Move: `docs/design/pixel5a-barbet.md`, `docs/pixel5a-bringup.md`, `docs/pixel5a-bringup-audit.md`, `docs/superpowers/specs/2026-11-14-pixel5a-integration-design.md`, `docs/superpowers/plans/2026-11-14-pixel5a-integration.md`
- Test: `tools/check-devices.py`

**Interfaces:**
- Produces: `soc = "qcom/sm7250"` for the device manifest; CODEOWNERS owner for `/socs/qcom/sm7250/` and later `/devices/google/barbet/`.

- [ ] **Step 1: Create the branch from the PR base**

Run: `git fetch origin main && git switch -c devices/barbet origin/main`
Expected: new branch, untracked docs preserved (`git status --short`).

- [ ] **Step 2: Write `socs/qcom/sm7250/soc.toml`**

Values: `vendor="qcom"`, `family="sm7250"`, `name="Qualcomm SM7250 (lito)"`, `arch="aarch64"`, `part_numbers=["SM7250"]`, kernel repository `https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git`, branch `master`, dts directory `arch/arm64/boot/dts/qcom`, boot format `android-boot-v2`, page size 4096, slots `ab`, `dtb_in_boot_image=true`, offsets base `0x00000000`, kernel `0x00008000`, ramdisk `0x10000000`, second `0`, tags `0x00000100`, dtb `0x18000000`, firmware directory `qcom/sm7250`.

- [ ] **Step 3: Add CODEOWNERS**

Append `/socs/qcom/sm7250/ @Gibbz`. Add `/devices/google/barbet/ @Gibbz` in Task 13.

- [ ] **Step 4: Commit the design, plan and family**

```bash
git add socs/qcom/sm7250/soc.toml .github/CODEOWNERS docs
git commit -m "socs/qcom: add the SM7250 family"
```

- [ ] **Step 5: Verify**

Run: `python3 -B tools/check-devices.py --no-tests`
Expected: exit 0 for the SoC tree (no barbet device yet).

### Task 2: Source lock and kernel config

**Files:**
- Create: `config/kernel/barbet-7.3.json`
- Create: `config/kernel/barbet.config`
- Test: `scripts/kernel/prepare-source.py` (offline), `sha256sum`

**Interfaces:**
- Produces: `release = "7.3.0-rc7"`, a prepared linux tree and `PREPARED.json`; the device manifest points at the lock and config.

- [ ] **Step 1: Record the exact archives**

Use the verified cache files and hashes:
- `linux-v7.3-rc7.tar.gz`, size `269820344`, SHA-256 `f789fc0d7b3713b6be23284f2a9c35bf41fb86785b24cfcc506c20d8ff712523`, URL `https://github.com/torvalds/linux/archive/refs/tags/v7.3-rc7.tar.gz`.
- `avicii-780934bbcc758694a9e4e8950eed9c040725df4d.tar.gz`, size `146568`, SHA-256 `5b151b749dc11c746b20268554a2cc5e72ba5f1a5a0f86fa66a6aca8c24ccc0f`, URL `https://github.com/lazybrar/avicii-mainline/archive/780934bbcc758694a9e4e8950eed9c040725df4d.tar.gz`.

- [ ] **Step 2: Write the lock**

`config/kernel/barbet-7.3.json` uses schema `org.projectluma.kernel-source/v1`, version `7.3-rc7`, release `7.3.0-rc7`, architecture `aarch64`, device_tree `qcom/sm7250-google-barbet.dtb`, the two sources above, a `base_config` naming `config/kernel/barbet.config` with its SHA-256, and the fourteen avicii `patches/0N-*.patch` entries (`source="overlay"`, exact SHA-256 from `reconstructed/overlay/avicii-mainline-780934bb.../patches`). Repository patches are appended by Tasks 4–10; the lock is not buildable until they exist.

- [ ] **Step 3: Write the config**

Copy the tested `driver-work/.config` (SHA-256 `43d3ace336d10e404c1d4847e5a0ca5d4a05e21179aa9d660ac1eefcbaf50c59`) to `config/kernel/barbet.config`. Clear `CONFIG_INITRAMFS_SOURCE` and any absolute path. Keep the release `7.3.0-rc7` (no local version).

- [ ] **Step 4: Verify preparation offline**

Run: `python3 scripts/kernel/prepare-source.py --lock config/kernel/barbet-7.3.json --cache /home/bronson/luma-barbet-work/barbet-integration/cache --output /tmp/barbet-prep --offline`
Expected: `PREPARED.json` with `release` `7.3.0-rc7` and the patch count; `git -C /tmp/barbet-prep/linux-7.3-rc7 status` shows the overlay applied. Repeat with a wrong hash and expect a non-zero exit.

- [ ] **Step 5: Commit**

```bash
git add config/kernel/barbet-7.3.json config/kernel/barbet.config
git commit -m "config/kernel: add the barbet 7.3 source lock and config"
```

### Task 3: Kernel builder and unit tests

**Files:**
- Create: `scripts/kernel/build-barbet.sh`
- Create: `tests/unit/test_barbet_kernel.py`
- Test: same

**Interfaces:**
- Consumes: the lock and config from Task 2.
- Produces: `OUTPUT_DIR/Image`, `OUTPUT_DIR/qcom/sm7250-google-barbet.dtb`, `OUTPUT_DIR/config`, `OUTPUT_DIR/receipt.json`; CLI `build-barbet.sh [--cache DIR] OUTPUT_DIR`.

- [ ] **Step 1: Write the failing tests**

Cases: `test_lock_pins_sources_and_patch_order`, `test_config_hash_matches`, `test_stale_receipt_is_rejected`, `test_failed_build_does_not_publish_outputs`, `test_offsets_match_soc`. Use temporary fixtures and fake `make`/`prepare-source` for the failure paths.

- [ ] **Step 2: Run and confirm RED**

Run: `python3 -B -m unittest discover -s tests/unit -p 'test_barbet_kernel.py' -v`
Expected: failures for missing builder/receipt.

- [ ] **Step 3: Implement the builder**

Prepare the tree with `prepare-source.py`, fail if the output exists or the lock/config hashes differ, copy `barbet.config` to the build tree, run `make olddefconfig`, compare the result to the committed config, build `Image` and `dtbs/qcom/sm7250-google-barbet.dtb` with `ARCH=arm64 LLVM=1`, and publish only after every check. Record the lock hash, patch count, config hash, release, Image SHA-256 and DTB SHA-256 in `receipt.json`.

- [ ] **Step 4: Run and confirm GREEN**

Run: `python3 -B -m unittest discover -s tests/unit -p 'test_barbet_kernel.py' -v`
Expected: PASS.

- [ ] **Step 5: Build the overlay-only baseline**

Run: `scripts/kernel/build-barbet.sh --cache /home/bronson/luma-barbet-work/barbet-integration/cache /tmp/barbet-base`
Expected: release `7.3.0-rc7`, Image and DTB present for the base plus the fourteen overlay patches. The full lock build with repository patches is Task 16.

- [ ] **Step 6: Commit**

```bash
git add scripts/kernel/build-barbet.sh tests/unit/test_barbet_kernel.py
git commit -m "scripts/kernel: add the barbet kernel builder"
```

### Task 4: Canonical board device tree (v161 target)

> **Patch-series rule for Tasks 4–10:** after writing each
> `patches/linux-barbet/7.3/000N-*.patch`, append its
> `{"source": "repository", "path": "...", "sha256": "..."}` entry to the
> `patches` array in `config/kernel/barbet-7.3.json`, in numeric order, and
> re-run `prepare-source.py --offline` to prove the whole series still
> applies cleanly.

**Files:**
- Create/Modify: `patches/linux-barbet/7.3/0001-arm64-dts-qcom-add-google-barbet.patch`
- Modify: `arch/arm64/boot/dts/qcom/sm7250.dtsi`, `sm7250-google-barbet.dts`, `Makefile` (inside the patch)
- Test: `dtc`, DTB comparison

**Interfaces:**
- Produces: `qcom/sm7250-google-barbet.dtb` whose decompiled nodes match the v161 DTB for the board, display, keys, UFS/USB, PMIC, GPU/SMMU and later subsystem nodes.

- [ ] **Step 1: Reconstruct the source**

Compare the v161 DTB (`/tmp/opencode/v161x/dtb`), the external include chain (`/home/bronson/luma-barbet-work/barbet-integration/wcn-v60-sleep.dts` and its includes), the repo `sm7250-google-barbet.dts`, and the `driver-work` tree. Write one board DTS plus the SM7250 shared nodes; exclude CS35L41/MI2S experiments and any node not used by v161.

- [ ] **Step 2: Build the DTB from the prepared tree**

Apply the board patch to the Task 2 tree and run the DTB build. Expected: `sm7250-google-barbet.dtb` produced without warnings beyond the documented `/memory`/DSI address-cell warnings.

- [ ] **Step 3: Compare against v161**

Decompile both with `dtc -I dtb -O dts` and diff. Expected: no functional differences; phandle renumbering may differ. Record any remaining difference in the patch README and require hardware validation for it. If the source cannot reproduce v161, stop and report instead of shipping a guessed DTS.

- [ ] **Step 4: Commit**

```bash
git add patches/linux-barbet/7.3/0001-arm64-dts-qcom-add-google-barbet.patch
git commit -m "patches/linux-barbet: add the barbet board device tree"
```

### Task 5: ST FTS SPI touch patch

**Files:**
- Create: `patches/linux-barbet/7.3/0002-Input-add-ST-FTS-SPI-touchscreen.patch`
- Source: `driver-work/drivers/input/touchscreen/stfts-spi.c`, `Kconfig`, `Makefile`
- Test: build + event capture

**Interfaces:**
- Produces: `CONFIG_TOUCHSCREEN_STFTS_SPI`, `st,fts-spi` compatible, input device with coordinates 0..1079/0..2399.

- [ ] **Step 1: Extract only the touch delta from the v161 tree**

Diff the touch files against the Task 2 prepared tree; exclude unrelated experiments. Keep the binding/YAML only if it is part of the tested source.

- [ ] **Step 2: Build the touch object and DTB**

Run: `make ARCH=arm64 LLVM=1 drivers/input/touchscreen/stfts-spi.o qcom/sm7250-google-barbet.dtb`
Expected: build passes; the DTS node resolves.

- [ ] **Step 3: Capture touch evidence**

On the device, `od -An -tx1 /dev/input/event2` while touching; record x/y ranges and release. Expected: real contacts and an empty final slot.

- [ ] **Step 4: Commit**

```bash
git add patches/linux-barbet/7.3/0002-Input-add-ST-FTS-SPI-touchscreen.patch
git commit -m "patches/linux-barbet: add ST FTS SPI touch"
```

### Task 6: Display and panel

**Files:**
- Create: `patches/linux-barbet/7.3/0003-drm-panel-barbet-display.patch`
- Test: DRM mode + visible brightness

**Interfaces:**
- Produces: `card0-DSI-1` enabled at 1080x2400; backlight device; no wrong-mode regression.

- [ ] **Step 1: Verify the current panel path**

Check whether v161 uses `panel-samsung-sofef00.c` with a barbet mode or a dedicated sofef01 driver. Record the exact mode and compatible.

- [ ] **Step 2: Add only what the tested display needs**

If a dedicated sofef01 driver is required, add it with the stock init sequence and mode; otherwise keep the existing panel and document the limitation. Do not ship the 1080x2280 Pixel 4 mode as “barbet.”

- [ ] **Step 3: Build and capture**

Build the panel/DTB, boot, and record `card0-DSI-1` state, mode, and visible brightness changes at low/mid/high.

- [ ] **Step 4: Commit**

```bash
git add patches/linux-barbet/7.3/0003-drm-panel-barbet-display.patch
git commit -m "patches/linux-barbet: add the barbet display mode"
```

### Task 7: GPU, SMMU and remoteproc

**Files:**
- Create: `patches/linux-barbet/7.3/0004-drm-msm-enable-adreno-620.patch`
- Source: `drivers/gpu/drm/msm/generated/a6xx.xml.h`, `drivers/remoteproc/qcom_q6v5_pas.c`, GPU/SMMU DT nodes
- Test: debugfs + render client

**Interfaces:**
- Produces: `gpu-initialized: 1`, GPU firmware loaded, `/dev/dri/renderD128` with a Phoc/Phosh client; no SMMU `-110`.

- [ ] **Step 1: Extract the GPU/SMMU/remoteproc delta**

Include the GPU enablement, SMMU vote clock/`qcom,adreno-smmu` fixes, zap/GPU firmware paths and the ADSP/modem PAS changes that are not part of the Wi-Fi patch. Exclude generated build artifacts.

- [ ] **Step 2: Build and boot**

Build `Image` and DTB; boot. Expected dmesg: SMMU probe success, Adreno/GMU probe, no `-110`.

- [ ] **Step 3: Verify a real render client**

Run `cat /sys/kernel/debug/dri/0/gpu | grep gpu-initialized` and `cat /sys/kernel/debug/dri/0/clients`; record `gpu-initialized: 1` and a render-node client.

- [ ] **Step 4: Commit**

```bash
git add patches/linux-barbet/7.3/0004-drm-msm-enable-adreno-620.patch
git commit -m "patches/linux-barbet: enable the Adreno 620 and GPU SMMU"
```

### Task 8: WCN3990, QRTR and modem memory

**Files:**
- Create: `patches/linux-barbet/7.3/0005-net-qrtr-and-barbet-modem.patch`
- Source: `net/qrtr/ns.c`, `net/qrtr/af_qrtr.c`, `drivers/remoteproc/qcom_q6v5_pas.c`, board DTS memory reservations
- Test: MPSS up + wlan0 traffic

**Interfaces:**
- Produces: modem/ADSP memory reservations, QRTR HELLO-gate fixes, WCN3990 board-2 alias and Wi-Fi firmware path.

- [ ] **Step 1: Extract the QRTR/PAS/WCN3990 delta**

Preserve the upstream QRTR commit messages and authors. Include the `modem_wlan_mem` reservation and any board-2/alias wiring used by v161. Exclude camera carveouts that are not needed.

- [ ] **Step 2: Build and boot**

Expected: `remote processor modem is now up`, no modem watchdog reset, no hard reset.

- [ ] **Step 3: Capture Wi-Fi**

Start the platform Wi-Fi service; record `wlan0` association, DHCP, ICMP and DNS. If the reservation hard-resets the phone, stop and report.

- [ ] **Step 4: Commit**

```bash
git add patches/linux-barbet/7.3/0005-net-qrtr-and-barbet-modem.patch
git commit -m "patches/linux-barbet: add WCN3990, QRTR and modem memory"
```

### Task 9: Speaker (TAS2562, QUIN TDM)

**Files:**
- Create: `patches/linux-barbet/7.3/0006-ASoC-qcom-barbet-speaker.patch`
- Source: `sound/soc/codecs/tas2562.c/h`, `sound/soc/qcom/sm8250.c`, `sound/soc/qcom/qdsp6/q6afe*.c/h`, `sound/soc/qcom/qdsp6/q6asm-dai.c`, `sound/soc/qcom/Kconfig`, board DTS
- Test: live amp registers + recorded tone

**Interfaces:**
- Produces: QUIN TDM RX with 8 slots at 12.288 MHz, mask `0x3`, S24 backend, DSP_A, paired TAS2562 (0x4c/0x4d), reset GPIO11/10 and LPI pinctrl re-apply.

- [ ] **Step 1: Extract the speaker delta**

Include the machine-driver fixup (`snd_mask_none`), the QUIN branch, the TAS2562 paired volume/pinctrl fixes, the `sleepstate` SMP2P node and the DTS nodes. Exclude CS35L41 and unused MI2S/headset paths.

- [ ] **Step 2: Build and boot**

Expected: no ASoC errors, both amps at `0x4c`/`0x4d` respond, port `0x9040` starts with `nslots=8 mask=0x3 bw=24`.

- [ ] **Step 3: Capture acoustic evidence**

Play a 2 kHz tone through the speaker sink; record with the mic. Expected: 2 kHz band at least 100x the no-tone baseline; both amps `PWR_CTRL=0x0c`, `TDM_DET=0x44`. Do not claim success from `aplay rc=0`.

- [ ] **Step 4: Commit**

```bash
git add patches/linux-barbet/7.3/0006-ASoC-qcom-barbet-speaker.patch
git commit -m "patches/linux-barbet: add the barbet speaker path"
```

### Task 10: Microphone (RT5514P)

**Files:**
- Create: `patches/linux-barbet/7.3/0007-ASoC-qcom-barbet-microphone.patch`
- Source: `sound/soc/codecs/rt5514*.c/h`, `include/sound/rt5514.h`, `sm8250.c` capture branch, board DTS
- Test: real capture samples

**Interfaces:**
- Produces: PRIMARY_TDM_TX_0 capture with one contiguous mode-3 1 MHz SPI burst; DMIC1 route; `arecord` real samples.

- [ ] **Step 1: Extract the mic delta**

Include the contiguous SPI transfer, the capture DAI branch, PLL-from-BCLK and the DTS routes. Exclude unrelated experiments.

- [ ] **Step 2: Build and boot**

Expected: card appears, `MultiMedia1 Mixer PRIMARY_TDM_TX_0=on`, `0x18001014=0`, `0x18002fc8` advancing.

- [ ] **Step 3: Capture real samples**

`arecord` 5 s; expected: nonzero RMS and thousands of distinct values, not silence. Record the command and result.

- [ ] **Step 4: Commit**

```bash
git add patches/linux-barbet/7.3/0007-ASoC-qcom-barbet-microphone.patch
git commit -m "patches/linux-barbet: add the barbet microphone path"
```

### Task 11: Boot initramfs and platform services

**Files:**
- Create: `config/mobile/barbet-initramfs/init_functions.sh`
- Create: `config/mobile/barbet-initramfs/hooks-extra/60-barbet-platform.sh`
- Create: `config/mobile/barbet-initramfs/luma-barbet/...` platform files
- Create: `config/mobile/barbet-initramfs.manifest`
- Test: manifest hashes, shell syntax, no firmware

**Interfaces:**
- Produces: an assembled initramfs tree containing the ADSP/audio service, ACP profile + udev rule, Wi-Fi/`tqftpserv` automation, resume hook, firmware checks and the shared OSTree resolver; no FP6 modem seed.

- [ ] **Step 1: Write the manifest and files**

List every file with mode, SHA-256 and origin, Fold-style. The init functions must leave `/boot` on the root filesystem and must not copy or write modem storage.

- [ ] **Step 2: Verify**

Run `sh -n` on every script; recompute every SHA-256 in the manifest; assert no firmware payload and no `modemst`/`fsg`/`fsc` write.

- [ ] **Step 3: Commit**

```bash
git add config/mobile/barbet-initramfs config/mobile/barbet-initramfs.manifest
git commit -m "config/mobile/barbet: add the boot initramfs and platform hooks"
```

### Task 12: Boot-image builder and unit tests

**Files:**
- Create: `scripts/mobile/build-barbet-boot-image.sh`
- Create: `tests/unit/test_barbet_boot_image.py`
- Test: same

**Interfaces:**
- Consumes: Task 3 `Image`/DTB, Task 11 initramfs, the FP6 Alpha 1 ramdisk base.
- Produces: `boot.img` (100663296 bytes) with header v2, page 4096, AVB `NONE`, rollback `1783209600`, `text_offset=0x80000`, standard LZ4 kernel, `boot_a` policy.

- [ ] **Step 1: Write failing tests**

Cases: `test_header_and_avb_policy`, `test_shared_resolver_required`, `test_wrong_input_rejected`, `test_oversize_preserves_existing_output`, `test_no_flash_side_effects`.

- [ ] **Step 2: Run RED**

Run: `python3 -B -m unittest discover -s tests/unit -p 'test_barbet_boot_image.py' -v`
Expected: failures for missing builder.

- [ ] **Step 3: Implement**

Patch `text_offset`, LZ4 standard frame, assemble the ramdisk from the Alpha 1 base + barbet initramfs + shared resolver, `mkbootimg` v2 with the SoC offsets, AVB hash footer `NONE`, atomic output.

- [ ] **Step 4: Run GREEN and round-trip**

Run the tests and unpack the result; verify header, DTB hash `761d489d…`, size and AVB info.

- [ ] **Step 5: Commit**

```bash
git add scripts/mobile/build-barbet-boot-image.sh tests/unit/test_barbet_boot_image.py
git commit -m "scripts/mobile: add the barbet boot-image builder"
```

### Task 12b: Full build and install set

**Files:**
- Create: `scripts/mobile/build-barbet-install.sh`
- Create: `tests/unit/test_barbet_install_set.py`
- Test: same

**Interfaces:**
- Consumes: Task 3 `Image`/DTB, Task 12 boot image, the shared ARM userdata image and its SHA-256.
- Produces: `boot.img`, the userdata image reference/copy, `SHA256SUMS`, `INSTALL.txt`; never flashes.

- [ ] **Step 1: Write the failing tests**

Cases: `test_sha256sums_cover_every_artifact`, `test_install_commands_only_touch_userdata_and_boot_a`, `test_wrong_device_or_userdata_rejected`, `test_oversize_boot_preserves_existing_set`, `test_no_flash_side_effects`.

- [ ] **Step 2: Run RED**

Run: `python3 -B -m unittest discover -s tests/unit -p 'test_barbet_install_set.py' -v`
Expected: failures for the missing script.

- [ ] **Step 3: Implement**

Accept `--userdata FILE` or a pinned release, verify its SHA-256 and device compatibility, call the kernel and boot-image builders, assemble the set atomically, and write `SHA256SUMS` plus `INSTALL.txt` with the exact commands and preserved partitions.

- [ ] **Step 4: Run GREEN and build a set**

Run the tests and produce `/tmp/barbet-install` from a clean cache. Verify every hash with `sha256sum -c` and inspect the boot image header/size.

- [ ] **Step 5: Commit**

```bash
git add scripts/mobile/build-barbet-install.sh tests/unit/test_barbet_install_set.py
git commit -m "scripts/mobile: add the barbet full build and install set"
```

### Task 13: Device manifest, install guide and tests

**Files:**
- Create: `devices/google/barbet/device.toml`
- Create: `devices/google/barbet/boot.toml`
- Create: `devices/google/barbet/firmware.manifest`
- Create: `devices/google/barbet/INSTALL.md`
- Create: `devices/google/barbet/SUPPORT.md`
- Create: `devices/google/barbet/tests/test_google_barbet.py`
- Modify: `.github/CODEOWNERS`
- Test: `tools/check-devices.py`

**Interfaces:**
- Consumes: Tasks 2, 3, 4–10, 11, 12.
- Produces: a strict `experimental` device with `maintainers=["@Gibbz"]`, `source_lock`, `config`, DTB, boot policy, `[initramfs]`, `[updates] ostree_ref="luma/1/aarch64/nightly"`, `[install] preserve`.

- [ ] **Step 1: Write the manifests**

Use the exact values from the spec and Tasks 1–12. Run Task 14's generator
first and copy its file count and manifest SHA-256 into `device.toml`.
The `[kernel]` section uses `source_lock = "config/kernel/barbet-7.3.json"`,
`release = "7.3.0-rc7"`, `config = "config/kernel/barbet.config"` and
`dtb = "qcom/sm7250-google-barbet.dtb"`; the patch series lives in the lock.
The `[initramfs]` section uses the FP6 Alpha 1 base
(`postmarketos-initramfs 3.12.0-r0`,
`ramdisk_sha256 = "944489269addf33f8cd5e8f305ccc368573396e26df03dc6ea63970242659a82"`),
`init_functions = "config/mobile/barbet-initramfs/init_functions.sh"` with
its SHA-256, and the shared OSTree package/source. `boot.toml` uses
`partition="boot_a"`, the offsets, the v161 command line, AVB `NONE`,
rollback `1783209600`, `dtbo policy="none"`.

- [ ] **Step 2: Write INSTALL.md**

Full install: extract firmware, `fastboot flash userdata`, `fastboot flash boot_a`, `fastboot --set-active=a`, `fastboot reboot`; preserved partitions; factory recovery; no `dtbo`/`vbmeta`/`modemst` writes. State that Android/userdata are erased.

- [ ] **Step 3: Write device tests**

Cases: lock/patch order matches, config hash matches, boot policy matches, install commands never touch preserved partitions, firmware manifest count/hash, initramfs manifest, no private data.

- [ ] **Step 4: Write SUPPORT.md**

Matrix with the verified areas and explicit battery/vibrate “not working” in PR 1; evidence pointers; limits.

- [ ] **Step 5: Verify**

Run: `python3 -B tools/check-devices.py` and the device test. Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add devices/google/barbet .github/CODEOWNERS
git commit -m "devices/google/barbet: add the experimental device"
```

### Task 14: Firmware manifest

**Files:**
- Modify: `devices/google/barbet/firmware.manifest`
- Create: `scripts/mobile/barbet/firmware-manifest.py`
- Test: `tools/check-devices.py`

**Interfaces:**
- Consumes: the operator's `/var/lib/luma/device-firmware-barbet` store.
- Produces: the manifest header count, directory/file rows and SHA-256; no payload.

- [ ] **Step 1: Implement the generator**

Walk the store, emit `H <count> - - -`, then `D`/`F`/`L` rows; refuse absolute paths, `..`, symlinks outside the store and non-regular files.

- [ ] **Step 2: Generate and verify**

Run the generator against the phone store; `tools/check-devices.py` validates count/hash. Recompute every SHA-256 independently.

- [ ] **Step 3: Commit**

```bash
git add scripts/mobile/barbet/firmware-manifest.py devices/google/barbet/firmware.manifest
git commit -m "devices/google/barbet: add the firmware manifest"
```

### Task 15: Status matrix and audit update

**Files:**
- Modify: `devices/google/barbet/SUPPORT.md`
- Modify: `docs/pixel5a-bringup-audit.md`
- Test: markdown links and commands

**Interfaces:**
- Produces: the user-facing status doc and the sanitized evidence narrative.

- [ ] **Step 1: Fill the matrix**

Rows: boot/install, display, touch, GPU, speaker, mic, suspend, Wi-Fi, battery, vibrate, keys, storage. Columns: status, evidence, limits.

- [ ] **Step 2: Update the audit**

Replace the stale “audio/GPU blocked” sections with the v161 results; keep the historical sections clearly dated. No raw captures, IMEI, SSIDs or credentials.

- [ ] **Step 3: Commit**

```bash
git add devices/google/barbet/SUPPORT.md docs/pixel5a-bringup-audit.md
git commit -m "docs: add the Pixel 5a support matrix"
```

### Task 16: PR 1 hardware validation and full install

**Files:**
- No source changes; evidence only.

**Interfaces:**
- Consumes: Task 12 boot image, Task 13 manifests, Task 14 firmware.
- Produces: a booted device and the evidence the PR description will cite.

- [ ] **Step 1: Build the full test set**

Run `scripts/mobile/build-barbet-install.sh --userdata <shared ARM userdata image> /tmp/barbet-install` from a clean cache; verify `SHA256SUMS` and inspect `boot.img` header/size. Expected: a complete set built from the lock, not the external bring-up tree.

- [ ] **Step 2: Full install**

Verify the device product/slot and the boot image hash. Use the shared ARM
userdata image (verify its SHA-256), then flash only `userdata` and
`boot_a`, set active `a`, and reboot. Because `userdata` is erased, copy
the operator's extracted firmware into
`/var/lib/luma/device-firmware-barbet` again before the first boot. Record
the exact commands and the preserved-partition check.

- [ ] **Step 3: Run the gates**

Display/mode, touch contacts, GPU client, speaker 2 kHz + mic capture, suspend cycle, Wi-Fi traffic, keys, storage. Record commands and outputs.

- [ ] **Step 4: Run the repository checks**

`python3 -B tools/check-devices.py`, the device tests, the unit tests, shell syntax and the boot-image round-trip. Record the actual result.

### Task 17: PR 2 — PM7250B charger

**Files:**
- Modify: `config/kernel/barbet.config` (`CONFIG_CHARGER_QCOM_PM7250B=y`)
- Modify: `patches/linux-barbet/7.3/0008-power-barbet-charger.patch` (board DTS node)
- Test: `power_supply` sysfs

**Interfaces:**
- Produces: `smb5`/`battery` power supplies with status and charge behaviour; only the charge-enable bit is written.

- [ ] **Step 1: Add the DT node**

PM7250B SMB5 at reg `0x1000`, USB/DC interrupts, `power-supplies` to the gauge (or omitted until Task 18), board supplies.

- [ ] **Step 2: Enable and build**

`CONFIG_CHARGER_QCOM_PM7250B=y`; build and boot; `ls /sys/class/power_supply`.

- [ ] **Step 3: Capture**

Plug/unplug, `status`, `charge_behaviour`; assert no write outside the charge-enable bit.

- [ ] **Step 4: Commit**

```bash
git add config/kernel/barbet.config patches/linux-barbet/7.3/0008-power-barbet-charger.patch
git commit -m "power: supply: enable the PM7250B charger"
```

### Task 18: PR 2 — battery gauge

**Files:**
- Create: `patches/linux-barbet/7.3/0009-power-barbet-battery.patch`
- Maybe: `drivers/power/supply/qcom_pm7250b_bms.c`
- Test: capacity/percent movement

**Interfaces:**
- Produces: a `capacity`/`voltage_now` battery supply, or a documented blocker with the driver and DT left out.

- [ ] **Step 1: Spike the gauge**

Read the PM7250B ADC/charger registers and the stock `google,bms` properties. Decide whether a safe minimal gauge is possible from documented registers.

- [ ] **Step 2: Implement or stop**

If safe, add the gauge driver and DT, enable it, and verify percent movement over charge/discharge. If not, leave it out and record the blocker in SUPPORT.md and the PR description.

- [ ] **Step 3: Commit**

```bash
git add patches/linux-barbet/7.3/0009-power-barbet-battery.patch devices/google/barbet/SUPPORT.md
git commit -m "power: supply: add barbet battery capacity"
```

### Task 19: PR 2 — DRV2624 haptics

**Files:**
- Create: `patches/linux-barbet/7.3/0010-input-add-drv2624.patch`
- Create: `drivers/input/misc/drv2624.c` (inside the patch)
- Modify: `drivers/input/misc/Kconfig`, `Makefile`, board DTS
- Test: FF effect + felt vibration

**Interfaces:**
- Produces: `CONFIG_INPUT_DRV2624`, I2C `0x5a`, reset PM8150 GPIO1, FF rumble/periodic.

- [ ] **Step 1: Write the minimal driver**

Regmap I2C, reset GPIO, the stock `ti,*` tuning properties, input FF device. No firmware upload.

- [ ] **Step 2: Build and boot**

`CONFIG_INPUT_DRV2624=y`; the device appears; no probe errors.

- [ ] **Step 3: Capture**

`fftest`/`evtest` effect plus human-confirmed felt vibration; no continuous buzz; suspend still works.

- [ ] **Step 4: Commit**

```bash
git add patches/linux-barbet/7.3/0010-input-add-drv2624.patch
git commit -m "input: add the TI DRV2624 haptics driver"
```

### Task 20: PR 2 validation and status

**Files:**
- Modify: `devices/google/barbet/SUPPORT.md`
- Test: hardware

- [ ] **Step 1: Run the PR 2 gates**

Charger status/percent, plug/unplug, suspend after haptics, no identity writes. Record the commands/output.

- [ ] **Step 2: Update the matrix**

Battery and vibrate move from “not working” to the actual status; any blocker is explicit.

- [ ] **Step 3: Commit and run checks**

```bash
git add devices/google/barbet/SUPPORT.md
git commit -m "devices/google/barbet: update the support matrix"
python3 -B tools/check-devices.py
```

## Self-Review

- **Spec coverage:** PR 1 covers source lock, config, builder, board DTS, touch, display, GPU, Wi-Fi, audio, mic, initramfs/platform, boot builder, device manifest, firmware, status and install. PR 2 covers charger, gauge and haptics. No spec goal lacks a task.
- **Step scan:** every step names a file, a command or a test; no “handle edge cases” or “TBD”.
- **Type consistency:** `build-barbet.sh [--cache DIR] OUTPUT_DIR`, `build-barbet-boot-image.sh`, `soc="qcom/sm7250"`, `source_lock="config/kernel/barbet-7.3.json"`, `config="config/kernel/barbet.config"`, `dtb="qcom/sm7250-google-barbet.dtb"`, `partition="boot_a"`, `ostree_ref="luma/1/aarch64/nightly"` are used consistently.
- **Review focus:** install preservation (Task 13/16), lock integrity (Task 2/3), boot-image failure (Task 12), acoustic audio (Task 9/16), battery/haptics/suspend (Task 17–20) each have a test or checklist.
- **Proportion:** the plan is task-level, not a code transcript; the exact patch bodies are left to the extraction tasks, which have comparison and hardware gates.
