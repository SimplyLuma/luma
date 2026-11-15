# Kernel

Luma builds one kernel for every ARM device, from one tree:
[SimplyLuma/linux](https://github.com/SimplyLuma/linux). There is no
per-device kernel and no device on another project's tree once it is on the
Luma base.

## Branches

| Branch | What it is | Commit |
|---|---|---|
| `luma-7.2` | The base: upstream stable 7.2.y (now v7.2.15). | `497791a0a96b` |
| `sm7250` | Qualcomm SM7250 family patches. Empty. | `497791a0a96b` |
| `sdm845` | Qualcomm SDM845 family patches. Empty. | `497791a0a96b` |
| `sc7280` | Qualcomm SC7280/QCM6490 family patches. Empty. | `497791a0a96b` |
| `zuma` | Google Tensor G3 (zuma) family patches. Empty. | `497791a0a96b` |
| `msm8953` | Qualcomm MSM8953 (Snapdragon 625/450/632) family patches. Empty. | `497791a0a96b` |
| `milos` | Qualcomm SM7635, on milos-mainline 7.1.2 (Alpha 1). | `c098393e36c0` |
| `milos-7.2` | Qualcomm SM7635 on `luma-7.2`: the Fairphone 6 kernel. Replaces `milos`. | `f20e69edcd1c` |

`497791a0a96b866d90add64041c47acdff49cae6` is the upstream `v7.2.15` commit.
`main` is the repository's fork point and is not used for builds.

- **Base** (`luma-<major>.<minor>`): the current upstream stable series,
  nothing else on it except the shared Luma config fragment
  (`luma/config/luma.fragment`: SELinux, binder, DMA heaps and the rest every
  device needs).
- **Family branches**, one per SoC family, named as the upstream kernel names
  the chip, lower case (`milos`, `sm7250`, `sdm845`, `sc7280`, `zuma`, `msm8953`, …).
  They carry only patches on top of the current base, plus the family's
  config fragment (`luma/config/<family>.fragment`) and its device trees.
  A family branch is never another project's tree and never an Android
  vendor kernel.

## Cadence

- Every upstream stable point release of the base series (7.2.y) is taken as
  it comes out; they carry the security fixes.
- The base moves to a new mainline series once the supported families have
  rebased and passed device tests. That is usually every other mainline
  release. A major version such as 8.0 is not special.
- The Nightly channel may run the next base early.
- A family whose patches do not rebase onto the new base in time drops its
  devices to `experimental` for that cycle. A family never holds a release.

## Release build

Each Luma kernel release merges every family branch onto the base, tags the
result `luma-kernel-<kernel release>` and builds from it:

- one arm64 kernel and one set of modules for every ARM device;
- `.config` = the base defconfig + `luma/config/luma.fragment` + every
  `luma/config/<family>.fragment`;
- one DTB per device, from that tree.

`device.toml` `[kernel] tag` names the release tag. Long term the kernel
moves into the one ARM OS image, and a device repository shrinks to firmware
plus its platform package.

**x86** stays on the Fedora kernel until Luma has its own shim signed through
the shim-review process; until then a custom x86 kernel would break Secure
Boot out of the box. The source tree can be shared with x86 before that.

## Family health

- Each family's patch count on top of the base is tracked. The goal is to
  shrink it by upstreaming.
- Planned CI: every family is rebuilt and boot-tested on top of the newest
  mainline and stable automatically, so conflicts surface before a base move.

## Contributing kernel work

Open a pull request against your family's branch in SimplyLuma/linux. If the
family has no branch yet, ask in an issue and Luma creates it at the current
base.

- Base it on the current Luma base, not on another tree.
- Patches are real commits: original authorship, `Signed-off-by` and licence
  and SPDX lines intact. Credit the source of anything taken from a vendor
  kernel (`Link:`, origin in the message).
- Device trees come from mainline or are written to be upstreamable: source
  `.dts` with an SPDX line, on the family `.dtsi`. No decompiled DTBs.
- Send patches upstream to mainline where possible.
- No kernel patches in SimplyLuma/luma. `device.toml` points at a Luma tag;
  `tools/check-devices.py` refuses source locks, `[kernel] patches` and new
  `patches/linux-*` directories for devices outside the transition list.

## Vendor kernels

A device that boots a vendor or Android kernel, or any tree other than the
Luma base, stays `experimental` until it is on the Luma base. Luma's own
Titan 2 Elite, HiBreak Pro, Pixel 9 and Pixel 9 Pro Fold ports are no
exception.

## Adopted devices

A device is adopted ("brought upstream") when Luma takes over its build:

- Luma builds the kernel and the device kit in its own pipeline and signs
  them with the Luma OS Release key.
- Luma publishes the device repository for hardware updates and ships boot
  images through `luma-device-boot` ([#392](https://github.com/SimplyLuma/luma/pull/392)).
- Luma owns both A/B boot slots on an adopted phone.
- Firmware that may not be redistributed is extracted from the phone at
  install and never shipped.
- The contributor stays the named maintainer.

## Update streams

| Stream | Source | Devices |
|---|---|---|
| OS | the one ARM OS image | every device |
| Apps | Flatpak (Depot) | every device |
| Hardware | the per-device repository: kernel, modules, boot image, firmware, platform | adopted devices only |

Experimental devices get the OS and apps only.

## Alpha listing gate

A device is listed on the website as Alpha when all of these hold, proven on
real hardware:

- boots with SELinux enforcing;
- display, touch, Wi-Fi, battery and charging, suspend and audio work;
- the install never writes modem or identity partitions;
- a clean install from the published ARM image plus the device kit;
- one OS update and one hardware update (boot image included);
- a known-issues list and a maintainer who answers.

Calls and mobile data are listed, not required. `supported` additionally
means Luma has the device and tests it every release.

## Transition

Existing devices keep their current kernels until they move:

| Device | Kernel today |
|---|---|
| Raspberry Pi 5 | Raspberry Pi tree, source lock |
| Surface Pro 12-inch | upstream 7.3 plus a patch overlay, source lock and `patches/linux-surface-pro12` |

The Fairphone 6, the reference family, is on `luma-7.2`
(`luma-kernel-7.2.15-luma1`). Each device leaves `TRANSITIONAL_KERNELS` in
`tools/check-devices.py` when it moves.
