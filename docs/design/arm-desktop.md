# ARM desktop and device integration

The desktop targets are `x86_64` and `aarch64`. ARM is a general desktop
architecture, not a simulator product. A compatible ARM virtual machine can use
the same desktop artifact. A Snapdragon laptop or tablet still needs its own
boot, kernel, device-tree and firmware qualification; an ARM CPU alone does not
establish compatibility.

## One operating system

Use the converged `SimplyLuma/luma` source tree for every target. Architecture,
session and device are separate selections: an ARM CPU does not imply a phone,
and Phosh does not require a separate application or platform implementation.
Shared fixes, app sources, libraries and build recipes belong in the common
root. GNOME Shell or Phosh, input/layout defaults, firmware and boot providers
are scoped profile inputs. A target must declare any unavailable capability;
it must not silently substitute another architecture's binary.

An implementation branch is temporary review work, not an ARM release fork.
Build both desktop architectures from the same integrated source revision.
Existing device releases may retain older qualified package sets during bring-up;
that qualification state must not become a permanent copy of the application
sources or an independent release process. Bring reusable mobile work into the
shared implementation and test the affected profiles.

## Architecture, capability profile and device profile

These selections answer different questions:

| Selection | Meaning | Current source contract |
| --- | --- | --- |
| Architecture | CPU ABI, Fedora base and UEFI loader | `desktop-targets.json`: `x86_64` or `aarch64` |
| Desktop capability profile | Which desktop capabilities this image contains | `desktop-profiles.json`: default `full`; explicit ARM-only `arm-preview` |
| Session | Shell and input/layout presentation | Shared GNOME desktop or existing handheld session/profile inputs |
| Device profile | A particular board's boot, kernel, DTB and firmware requirements | Existing device infrastructure; a desktop external-provider extension still needs implementation and qualification |

The `arm-preview` capability profile declares Android applications, Windows
applications and casting unavailable. Their engine/provider RPMs are excluded;
Relay, the application installer, APK metadata support and the shared application
baseline remain selected. This does not create a device-specific kernel profile
or weaken signed app-bundle admission. The image records its actual profile and
capabilities under `/usr/share/luma/desktop-capabilities.json`.

The default remains `full` on both architectures. A future device profile may
need the same capability profile, or a different explicitly declared selection;
it must never infer an unavailable engine is available from the presence of its
launcher or from a working VM.

## Shared build inputs

`config/os/desktop-targets.json` selects the immutable Fedora base and EFI
loader for each architecture. `scripts/os/lib/desktop_target.py` resolves package
selections from the common desktop pins, preserving versions and releases while
selecting the architecture suffix. Noarch packages stay shared. Hardware scope
excludes Intel crash logging from ARM; the explicit capability profile handles
preview exclusions. Other missing inputs remain unmet build requirements.

Inspect a target before composition:

```sh
python3 scripts/os/lib/desktop_target.py --arch aarch64 --profile arm-preview plan
python3 scripts/os/preflight-desktop.py --arch aarch64 --profile arm-preview --package-dir /path/to/rpms
```

The normal package collector, image builder and Atlas media builder accept the
selected architecture. Use `--profile arm-preview` or `LUMA_OS_PROFILE` where a
capability profile is selected. Package headers, hashes, normal component checks,
the signed offline application baseline and release gates remain mandatory.
Native CI can reuse checked toolkit artifacts only when their source closure,
complete archive digest, RPM identities and output hashes match the dependent
build. A changed label never turns an x86 binary into ARM.

## Kernel and installer boot cohort

The generic desktop consumes Fedora's kernel. Luma does not maintain a desktop
kernel fork. The installed payload's kernel provider and the Atlas installer's
boot/runtime provider are related inputs with different packaging roles.

Atlas's runtime must admit a coherent, checksum-pinned upstream Fedora cohort:
its selected kernel or UKI provider, matching module packages, firmware and the
architecture's EFI chain. Fedora's ARM Lorax path may require a UKI/DTB-loader
provider rather than the ordinary `kernel-core` packaging used by a different
boot path. Select the compatible provider variant at the pinned kernel release;
do not install both conflicting variants or let dependency resolution pull a
second kernel from the rolling repository. Required extra/internal modules are
part of the pinned cohort when the selected runtime requires them.

The source contracts are `config/install/atlas/runtime-kernel.env` and its ARM
counterpart `runtime-kernel-aarch64.env`, consumed through the shared media
helpers. The final artifact's qualification record must state the actual provider
RPMs, hashes, kernel release, module set and EFI files. A matching NVR or successful
RPM transaction alone does not prove that its installer boots, installs, updates
or rolls back. Secure Boot support needs separate evidence.

For hardware requiring another provider, consume that upstream/device project's
kernel and firmware with its updates, notices and redistribution rules. Extend
the existing device infrastructure to describe the external provider, compatible
userspace release, DTBs, firmware acquisition and recovery procedure. Do not
invent Luma kernel tags for upstream binaries. Existing Fairphone contracts must
remain intact; they do not automatically qualify a desktop Snapdragon device.

## Qualification host

The shared release gate selects ARM `virt`/`ttyAMA0` or x86 `q35`/`ttyS0`.
Matching hosts with KVM use CPU passthrough; other hosts use QEMU emulation.
Every image, install, update and rollback check still applies under that choice.
Libvirt emulator and UEFI capabilities are admitted before VM allocation and
saved with the gate evidence.

A provisioned host normally supplies its architecture emulator and firmware.
An operator can instead select an admitted native-host QEMU executable through
`LUMA_OS_GATE_EMULATOR` and a matching upstream pflash firmware pair through
`LUMA_OS_GATE_UEFI_CODE` and `LUMA_OS_GATE_UEFI_VARS`. The gate records their body
hashes; libvirt creates separate writable NVRAM for every VM. The operator must
retain provider/package provenance and arrange normal libvirt access and SELinux
labels. These tool selections do not waive release checks or qualify hardware.

## Imager and website handoff

Imager should select a verified artifact and a compatible device/boot profile.
It must not insert a kernel into a signed ISO while continuing to present the
original image's digest or signature. A different boot provider may require a
separately composed, verifiable artifact from the same shared desktop source.
An additive device bootstrap is valid only when its own trust, compatibility and
recovery contract is explicitly supported by that provider and by Imager.

The artifact handoff needs:

- Architecture, capability profile, source revision and immutable build/version identity.
- Published image URL, exact byte size, SHA-256, signature and pinned trust issuer.
- Actual tested VM or device models, firmware mode, storage requirements and support limits.
- Exact payload and installer kernel/UKI provider cohort, modules, DTBs, firmware terms and notices.
- Installation method, partition-preservation rules, recovery and rollback procedure.
- OSTree ref and signed graph endpoint for the selected architecture.
- Any device bootstrap/provider artifact's independent identity and compatibility constraints.

Use the published, signed per-architecture media index as the download discovery
input. Existing x86 paths remain `/media/<channel>/latest.json`; ARM indexes use
`/media/aarch64/<channel>/latest.json`. Each selected entry must match the requested
architecture, be public and currently downloadable, and retain its own verified
hash, size and signature evidence. An absent or unavailable ARM entry means ARM
is unavailable; never offer the x86 image as a fallback. Artifact identity must
include architecture, not merely a build ID or filename shared by two targets.
Website redirects and Imager must resolve the selected entry's actual URL.

Both architectures share the canonical signed OSTree repository. Their refs are
`luma/1/x86_64/<channel>` and `luma/1/aarch64/<channel>`; ARM graph URLs are under
`/os/graph/aarch64/`, while x86 graph URLs remain under `/os/graph/`. The architecture
of the graph, its signed trusted comment and the selected update ref must agree.
Use the release's actual configured host and channel; this document supplies no
live ARM download or signature.

The website's download page already exposes an ARM64 choice in its presentation.
It must remain unavailable until a qualified ARM artifact is actually published
and admitted to its architecture-specific download metadata. The website frontend
and standalone Imager are separate products; this repository's `website/` contains
branding, not their download-selector implementations.

## Qualification boundaries

An Apple Silicon VM presents virtual ARM hardware; it does not require Asahi's
bare-metal boot provisioning. A successful M4 VM test would qualify that VM
configuration and artifact, not a native M4 installation. Bare-metal Apple support
requires Asahi-compatible provisioning and model-specific support; a generic USB
ISO does not replace that process. Follow the upstream
[distribution guidelines](https://asahilinux.org/docs/alt/policy/),
[boot-process guide](https://asahilinux.org/docs/alt/boot-process-guide/) and
[installation FAQ](https://asahilinux.org/docs/project/faq/).

Surface and Snapdragon ThinkPad compatibility likewise require model-specific
boot, storage, display, input, wireless, power and recovery tests. Do not advertise
a model as supported based on the CPU name or a generic VM result.

No ARM desktop download, physical-device compatibility, Secure Boot qualification,
or update delivery is established by this design document. Those claims belong
to the exact artifact's completed qualification and public release records.
