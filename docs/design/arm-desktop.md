# ARM desktop and device integration

The desktop targets are `x86_64` and `aarch64`. ARM is a general desktop
architecture, not a simulator product. Virtual machines are one qualification
environment. A particular Snapdragon laptop or tablet still needs its boot,
kernel, device-tree and firmware support checked on that model.

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

## Shared build inputs

`config/os/desktop-targets.json` selects the immutable Fedora base and EFI
loader for each architecture. `scripts/os/lib/desktop_target.py` resolves
both package selections from the existing desktop pin list. It changes only
the architecture suffix, preserving the exact version and release. Noarch
packages stay shared. Explicit hardware scope excludes Intel crash logging
from ARM. An unavailable ARM package is an unmet requirement, not permission
to substitute an older handheld preview or silently remove an application.

Inspect the requirements without composing an image:

```sh
python3 scripts/os/lib/desktop_target.py --arch aarch64 plan
python3 scripts/os/preflight-desktop.py --arch aarch64 --package-dir /path/to/rpms
```

Preflight verifies RPM headers and records file hashes. It exits unsuccessfully
for missing inputs or conflicting builds. It does not qualify boot or updates.
The normal verified package pool and offline app baseline remain mandatory.
`build-image.sh --arch aarch64` and `collect-packages.sh --arch aarch64` select
the ARM requirements. A cross-architecture build needs actual ARM execution
(native Linux builder or explicitly configured emulation); changing a label
does not provide a compiler or make an x86 binary into ARM.

Native source checks run on both architectures. The initial native package
matrix builds Search and GTK3 on both, with normal RPM checks. This is the
first package group, not coverage of every desktop component. The remaining
toolkit, shell, application, compatibility and browser inputs must be brought
through native package checks before admitting an ARM desktop release. In
particular, the current Viola builder is x86-only. ARM app bundles must carry
ARM refs and signatures; x86 offline bundles are refused.

## Kernels and profiles

The generic image uses Fedora's kernel. Luma will consume an upstream or
device project's kernel and firmware when a device requires them, preserving
that provider's updates, notices and redistribution rules. This work does not
create a Luma desktop kernel fork.

Extend the existing device/profile infrastructure for desktop devices. Its
phone-specific kernel-tag assumptions must be extended to represent a pinned
external kernel provider; do not invent a Luma kernel tag for an upstream
binary. Existing Fairphone kernel, firmware and boot contracts remain intact.
Each device's release record must bind the userspace image to the exact
provider packages, modules, DTBs, firmware policy and recovery instructions
used for qualification. Modules and initramfs must match the selected kernel.

## Imager handoff contract

Imager should select a verified image/profile combination. It must not patch
files into a signed ISO and continue presenting the original image's digest
or signature as proof of the modified bytes. Where different kernels require
different boot media, compose a separately verifiable image from the shared
desktop source. Keep the architecture and device's update stream explicit.

The future Imager handoff must include:

- Image URL, byte size, SHA-256, signature/trust issuer and source revision.
- Architecture, firmware/boot requirements and actual tested environments.
- Compatible profiles, exact kernel provider and firmware acquisition terms.
- Installation method, preserved partitions, recovery and rollback procedure.
- Update ref and signed graph endpoint for this architecture/profile.
- Known unsupported features and the evidence needed to raise support tier.

Apple Silicon **VMs** use ARM virtual hardware and do not need Asahi. Installing
directly on Apple hardware needs Asahi's boot provisioning and model support;
writing the generic ISO to USB is not an equivalent installation method. See
[Asahi's distribution guidelines](https://asahilinux.org/docs/alt/branding/)
and [installation FAQ](https://asahilinux.org/docs/project/faq/).

No ARM desktop download, bare-metal compatibility, Secure Boot qualification,
or update delivery is established by this design document. Those claims belong
to the exact image's completed qualification and release records.
