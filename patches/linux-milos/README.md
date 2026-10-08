# Milos kernel diagnostics

These patches target the external Milos kernel used by the Fairphone 6
physical-device build.  They are not part of Luma's shared desktop/mobile
presentation layer and must never be applied to desktop kernels.

`0001-drm-msm-a810-disable-ifpc-diagnostic.patch` is a one-variable diagnostic
against the exact `v7.1.2-milos` source (`dfe0125e73541d1984e4d2d37bd069a036bf0451`).
It disables A810 idle power collapse while retaining cached coherency, hardware
APRIV, and preemption.  The expected cost is higher idle power consumption.

`0002-drm-msm-a810-gmu-timeout-diagnostics.patch` is the next,
behavior-neutral diagnostic against that same pinned source. The retained FP6
journals establish that GMU firmware initialization times out before the OOB,
GPU/DPU hangcheck, and CX-collapse failures. The patch adds A810-only register
and clock reporting after that existing timeout has fired. It does not change
boot selection, power sequencing, timeouts, IFPC, or recovery.

Fairphone's Android 16 Gen8.3 definition does not advertise GMU warmboot, while
the Milos DRM driver selects warmboot for A810 whenever TCM retention reads as
one. This is a lead, not yet a fix. The diagnostic must first establish which
path fails and what state the firmware and power registers actually reached.

The first RAM-only run of that diagnostic selected a cold boot with TCM
retention `0`, eliminating warmboot as the initiating condition. The trace
showed firmware version `0x50010012`, init result `0`, RPMh state `0xf`, both GX
off bits set, 350 MHz GMU and 200 MHz hub clocks, and a retained log write
pointer that advanced to `5`. Firmware therefore began executing but did not
complete initialization. Waydroid was stopped at the first timeout; the later
DPU recovery still occurred about ten seconds afterward. The phone was then
normally rebooted out of the RAM kernel without writing a partition.

`0003-drm-msm-a810-use-gen8-3-ahb-fence-range.patch` is the resulting
one-variable behavior test and applies after patch 0002. Fairphone's published
Android 16 Gen8.3 driver programs the GMU AHB fence lower bound to `0x8a0`,
while Milos applies the generic A8xx `0x8c0` bound to A810. Patch 0003 matches
the authoritative Gen8.3 value only for A810 and retains the timeout
diagnostics. Its RAM-only boot failed before Android: the native Luma session
produced a GMU fence-interrupt storm with more than 81,000 callbacks suppressed,
then a ring-0 hangcheck attributed to GNOME Shell. Patch 0003 is rejected and
must not be carried into another candidate.

`0004-drm-msm-a810-use-gen8-3-gmu-boot-rate.patch` is an alternative branch
after patch 0002, not a continuation of patch 0003. Fairphone boots Gen8.3 at
the upper 650 MHz GMU power level. Milos requests 200 MHz, which rounded to the
350 MHz lower A810 OPP in the physical failure trace. Patch 0004 selects 650 MHz
only while bringing up A810; it leaves every other GPU and the firmware-managed
frequency table unchanged. Its builder mode explicitly removes patch 0003
before applying patch 0004 when reusing the diagnostic build tree.

Its first RAM-only physical run completed native Luma Shell and one
software-isolated cold TikTok launch with more than five minutes of stable
process identity, a 44.4-degree Celsius cold-start peak that settled to 37
degrees, and zero GMU, GPU, or DPU fault signatures. This is enough to retain
the candidate for repetition, not enough to promote it: the Android boundary
remained CPU-rendered at 30 Hz and its cold-start frame statistics were still
poor. Repeat cold boot, display-power, interactive direct-render, and idle
power gates remain open.

Promotion requires physical FP6 evidence showing:

- repeated Luma Shell suspend/wake remains healthy;
- native Waydroid surfaces run without an Adreno hangcheck;
- Android applications are managed as ordinary Luma application windows; and
- idle-power measurements are understood.

The exact `cross-x86_64` baseline and the IFPC diagnostic have both passed
RAM-only boot with Linux USB, Fedora userspace, and Luma Shell returning. The
baseline also passed a 24-minute idle soak at 37 degrees Celsius with zero GPU
hangchecks or GMU timeouts. Native Waydroid workload testing remains gated and
must not be inferred from these idle results.

Keep the proven postmarketOS control boot image available in slot B recovery
materials until a replacement kernel has passed those checks.

The FP6 boot artifact must zero-pad the raw ARM64 `Image` through the
`image_size` declared at header offset 16 before gzip compression. The generic
kernel `Image` target may end at the final file-backed byte instead.

The FP6 bootloader's RAM-only `fastboot boot` path has been proven with the
known-good postmarketOS control image. Both the padded IFPC diagnostic and a
padded, otherwise unmodified kernel rebuilt with Fedora's Clang/LLD 22.1.8
failed before Linux USB or persistent userspace logging appeared. This A/B
result isolates the current failure to kernel-build reproducibility rather
than the IFPC source change. Do not draw an IFPC conclusion or persist a rebuilt
kernel until an unmodified baseline produced in the matching Alpine/
postmarketOS build environment passes the same RAM-only boot gate.

Do not compile this kernel on the FP6. Sustained compilation alone reproduced
the A810/GMU hangcheck path with Waydroid inactive, made the device warm, and
blocked `gnome-shell`. Build in an isolated environment off the phone and
reserve the phone for bounded RAM-only acceptance tests.

The proven package config also excludes the native-host-only
`CONFIG_CC_CAN_LINK=y` capability. Use the repository's default
`cross-x86_64` postmarketOS build profile: an x86_64 Alpine userspace
cross-compiling ARM64 with the pinned Clang/LLD packages. The retained
`native-aarch64` profile is a negative diagnostic control, not a promotion
build.

## FP6 audio topology

`0025-arm64-dts-qcom-milos-enable-fp6-speaker-dai.patch` is independent of
the rejected GPU experiments. It exposes the existing Senary MI2S playback
link to the two already-described AW88261 amplifiers. The amplifier at I2C
address `0x34` serves the top speaker/receiver and the amplifier at `0x35`
serves the bottom speaker. The patch does not add a codec driver, route a
microphone, alter gain, or install proprietary firmware.

The DT-only candidate exposed the expected PCM, but its first control query
also found an ALSA null dereference in the retained audio modules. Current
Catcrafts `combined-stable` history isolated the three missing fixes:

- `0026` / upstream development commit `692c0cd`: Senary MI2S machine support;
- `0027` / `79c7597`: AW88261 format negotiation and power-up behavior; and
- `0028` / `d571dd4`: start the Q6APM graph during prepare.

All three apply cleanly to the pinned 7.1.2 tree. Exact-ABI modules built from
that tree now pass physical raw-ALSA and PipeWire output on both amplifiers;
the prebuilt modules from another kernel were correctly rejected and were
never force-loaded. The normal speaker path uses non-mmap PipeWire access and
2400-frame periods. Receiver mode is routed beneath the same stable sink to
avoid an ACP profile teardown that stalls the single AudioReach PCM.

Microphone capture remains a distinct Luma integration gap, but it is no
longer a greenfield driver design. Current Catcrafts history contains the
SoundWire paging, WCD9378 codec, FP6 DT, clock-stop, gain-TLV, and mainline-v1
sync series (`8ed47c2`, `2fd346e`, `5c9c899`, `9df8d66`, `e9a6b53`,
`5694c4a`, `82c43dd`, `08f135e`) and reports physical microphone operation.
Luma must backport that exact series to its running ABI, RAM-boot it, and pass
capture/call acceptance. Do not represent working speaker output or an
enumerated capture PCM as microphone or call-audio support.
