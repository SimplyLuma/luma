# Building Luma

Luma shares its native application and platform source across desktop and handheld profiles. Images are built for an architecture and device profile; an x86_64 desktop ISO is not an ARM phone installer. This repository contains the OS and bundled applications. Office, Creative, and the standalone Imager are separate projects. The OS installer and Photos/Darkroom remain part of this repository.

These instructions describe the source-owned build entry points. A complete release also needs the pinned upstream inputs, independently built package artifacts, an admitted offline application baseline, and signing material owned by the builder. A fresh clone does not contain those binary inputs or release credentials. Build outputs are not evidence of device qualification.

## Source layout

| Path | Purpose |
| --- | --- |
| `src/luma-platform/` | Native Luma UI, Semantics, Application Kit, and SDK |
| `src/prairie-core/` | Shared native bundled application source |
| `src/external/charlie/`, `src/external/viola/` | Bundled mail and browser integration source |
| `src/luma-installer/`, `src/luma-installer-atlas/` | Application installation services and OS installer |
| `scripts/packages/`, `packaging/rpm/` | Component package builders and RPM recipes |
| `packaging/flatpak/`, `scripts/depot/` | Application/runtime packaging and signed repository tooling |
| `patches/` | Maintained upstream patch families; preserve upstream author and license notices |
| `config/desktop/`, `config/os/` | Desktop package pins and release contracts |
| `config/mobile/`, `scripts/mobile/` | Device-specific handheld build profiles |
| `image/luma-desktop/` | Fedora Atomic desktop image definition |
| `tests/` | Source, package, integration, and installation checks |

See [THIRD-PARTY-SOURCES.md](THIRD-PARTY-SOURCES.md) for upstream attribution. Individual component notices and licenses take precedence over the default license for original Luma code.

## Native platform development

The platform uses Meson. Its current source declares Meson >= 1.3.0, GLib/GObject/GIO >= 2.80, GTK >= 4.18, libadwaita >= 1.7, and wayland-client >= 1.23. Install the corresponding development packages, a C17 compiler, pkg-config, Ninja, Python, and the introspection tools required by the selected subprojects.

From the repository root:

```sh
meson setup build/platform src/luma-platform
meson compile -C build/platform
meson test -C build/platform --print-errorlogs
```

Use Meson's installation prefix or a staging `DESTDIR` when testing installation. Development builds should not replace a distribution's system GTK or GNOME packages.

`src/prairie-core/` is a Python application collection packaged by `scripts/packages/build-prairie-core-apps.sh`; it is not a separate Meson project. Its package checks require the pinned platform RPM, icon theme, and Python dependency packages. The source builder names the prerequisites and refuses missing artifacts.

## Component RPMs and Flatpaks

On a suitable Fedora Linux builder, the platform RPM entry point is:

```sh
scripts/packages/build-luma-developer-platform.sh
```

It supports `x86_64` and `aarch64` using the corresponding builder image. Package pins and builder container digests are recorded in `config/desktop/inputs.env`. Native AArch64 and x86_64 package dependencies must match the selected target architecture; setting an architecture variable does not supply missing cross-compilation or device dependencies.

Component builders under `scripts/packages/` create RPM/SRPM outputs under `build/packages/`. Build the prerequisites named by each script before its dependents. The shared application package builder is:

```sh
scripts/packages/build-prairie-core-apps.sh
```

Flatpak app definitions live in `packaging/flatpak/apps/`; platform/SDK definitions live in `packaging/flatpak/runtime/`. `scripts/depot/` contains their source preparation, build, verification, and publication tooling. Use the specific manifest's declared sources and permission contract. Publication requires separate authorized signing and repository configuration; compiling an application does not publish it.

## Viola's external engine dependency

`src/external/viola/chromium-linux/appkit-spike/` contains the shared native host and Application Kit integration. `scripts/packages/build-viola-browser.sh` supports both `x86_64` and `aarch64`; `LUMA_TARGET_ARCHITECTURE` selects the target and defaults to the builder's machine architecture. Each architecture has its own admitted engine archive, release and SHA-256 in that script.

Run on a builder for the target architecture and supply its matching engine archive, for example:

```sh
LUMA_TARGET_ARCHITECTURE=aarch64 \
LUMA_VIOLA_ENGINE_ARCHIVE=/path/to/admitted-aarch64-engine.tar.zst \
scripts/packages/build-viola-browser.sh
```

This repository is not a complete Chromium/browser-engine source checkout. The matching engine source, its upstream licenses, and its build recipe must be obtained separately; an arbitrary Chromium binary cannot substitute for the admitted archive. Package builds and source checks do not establish successful native rendering; qualify the resulting browser in the target desktop environment before release.

## Desktop images and installation media

Two source-owned paths exist:

- `scripts/build-luma-desktop.sh` builds the native component stack and composes a development VM image through `scripts/vm/compose-desktop-image.sh`.
- `scripts/os/build-image.sh` builds the current Fedora Atomic desktop image from `image/luma-desktop/Containerfile`. `scripts/os/export-ostree.sh` exports its OSTree candidate; `scripts/os/gate.sh` performs release qualification; `scripts/os/build-staff-media.sh` composes installation media using the Atlas installer source/RPM inputs.

The OS pipeline requires a dedicated Fedora build environment with Podman, RPM tooling, OSTree/rpm-ostree, GPG/minisign tooling, and the dependencies declared by the invoked scripts. Configure the pipeline's output storage and signing locations for your own builder; never reuse another publisher's private keys. Review each script's capacity and host-state checks before execution.

The current image builder requires a signed offline application baseline using `--app-baseline`. Its contract is `config/os/first-party-app-baseline.json`; `scripts/os/lib/app_baseline.py` verifies the supplied input. `scripts/os/collect-packages.sh` admits package drops matching `config/desktop/packages.txt`. Missing baseline bundles or package artifacts are explicit prerequisites, not files recreated by a plain Git checkout.

Example image invocation, after those inputs have been prepared:

```sh
scripts/os/build-image.sh --build-id YOUR_BUILD_ID --channel beta \
  --base pinned --app-baseline YOUR_SIGNED_BASELINE_DIRECTORY
```

Installation-media composition additionally requires the exported candidate and matching Atlas source/RPMs:

```sh
scripts/os/build-staff-media.sh --atlas-source YOUR_ATLAS_SOURCE \
  --atlas-rpms YOUR_ATLAS_RPM_DIRECTORY --output YOUR_OUTPUT_ISO \
  --channel beta --test-candidate YOUR_BUILD_ID
```

`--test-candidate` produces test media; it does not declare a release ready. Retain the build's source identity, package manifest, SBOM, provenance, and qualification results. Dirty development inputs are explicitly marked and refused by normal publication paths.

See [release delivery](docs/operations/release-delivery.md) for the separate OS, application and R2 installation-media publication contracts, verification requirements and retention rules.

## Desktop architectures

Desktop source changes target both `x86_64` and `aarch64`. The ARM desktop
bring-up uses the same source versions and release requirements as x86;
existing phone preview pins do not establish desktop ARM parity.

```sh
python3 scripts/os/lib/desktop_target.py --arch aarch64 plan
python3 scripts/os/preflight-desktop.py --arch aarch64 --package-dir YOUR_RPM_DIRECTORY
```

The normal image, package-pool and Atlas media entry points accept
`--arch x86_64|aarch64`. Atlas uses Fedora’s architecture-specific UEFI boot
chain and an unmodified, checksum-pinned Fedora kernel matching the payload.
Its build host must execute the selected architecture natively or through
registered binfmt emulation; x86 remains the default.
Offline app inputs must match the selected architecture. A complete ARM image
also needs every resolved native package, a signed ARM app baseline, installer
media and actual boot/install/update qualification; the first architecture
checks and package matrix do not establish those results. See
[ARM desktop and device integration](docs/design/arm-desktop.md) for the
shared-source rules, upstream kernel policy and Imager handoff requirements.

## Handheld profiles

`config/mobile/` and `scripts/mobile/` contain device-specific native Linux integration. The Fairphone 6 rootfs entry point is `scripts/mobile/compose-fp6-rootfs.sh`, which requires root on a Fedora 44 AArch64 builder. It consumes the pinned shared packages plus handheld compositor, greeter, kernel, firmware, and device dependencies selected by the profile.

```sh
scripts/mobile/compose-fp6-rootfs.sh YOUR_OUTPUT_DIRECTORY
```

A rootfs archive alone is not a flashable universal mobile image. Bootloader, kernel, device tree, firmware redistribution terms, storage layout, and recovery procedures are device-specific. Inspect the corresponding profile and upstream source pins before building. Source availability and a successful userspace build do not establish current physical hardware support or safe installation on another device.

## Checks and contributions

Run component tests for the code you change, then the applicable package/source and image gates. Hardware-dependent checks require the named real environment; keep skipped or failed checks visible. Preserve third-party license texts, copyright notices, and patch author headers when rebasing. Separate app updates and OS releases must retain their own source/package identity and signature verification.

## Checking a public source checkout

Run `bash tools/check-repository.sh` for the public documentation/source contract,
Notes and application-kit contracts, and device manifests/tests. Install Python's
`jsonschema` package as required by the device validator. This checks the public
OS/application tree; separate Creative and Office repositories are not required.

For a source drop, run `scripts/os/check-source-before-drop.sh SOURCE_DIR DROP_DIR
REVISION` from a Git checkout at that full revision. It also runs the complete
`tests/unit` and `tests/depot` suites and records evidence only on success.
Use the script's documented Fedora dependencies, a D-Bus session, and a display
(or `xvfb-run` on a headless builder) for the GTK tests. A successful source check
is not package, device, installer or release qualification. `--backfill` records
missing historical checks as unavailable; it is not the normal public workflow.

The complete source-drop suites also include GTK/EDS/LumaAppearance integration
and root-owned publisher boundary tests. Run them in the isolated native builder
with the Luma libraries installed, as root inside that builder; a stock host's
missing typelibs or unprivileged account cannot qualify those boundaries.
Creative/Office/standalone Imager producers are separate products: their absent
source trees are not required by the OS source check. Their remaining shared
catalogue/update metadata checks still run; external engine suites report an
explicit product-scope skip when those sources are not present.

Use a private, root-owned scratch directory outside the builder's home for
`TMPDIR` when running publisher custody tests. On container images where
`/root` is a symlink, provide a real root home for those filesystem-boundary
fixtures. GTK geometry tests need a display large enough for their desktop
windows, for example `xvfb-run -a -s '-screen 0 2560x1600x24'`, with
`GDK_SCALE=1`. These are builder-fixture requirements, not changes to the
installed product's filesystem or display settings.

For filesystem boundary tests in a layered container, bind-mount the private
scratch directory from one real filesystem and remove inherited default ACLs
from that fixture directory only. Overlay layers can expose different device
IDs for directories and files; the safety checks intentionally reject
cross-device traversal. Keep those guards enabled rather than changing
expected results to fit the container's overlay or shared-drive permissions.

The explicit `arm-preview` desktop profile is available only for `aarch64`.
Select it with `--profile arm-preview` on the desktop preflight, package collector
and image builder, or set `LUMA_OS_PROFILE=arm-preview`. It uses the same source,
Fedora kernel, package checks and signed application baseline as the full desktop.
Android applications, Windows applications and casting are unavailable in this
preview; their engine/provider packages are excluded. Relay, the application
installer and the rest of the shared application baseline remain selected.

The default profile is `full` for both architectures. The preview never changes
that default or bypasses first-party bundle admission or image signing. Every
new image records `/usr/share/luma/desktop-capabilities.json` and its profile;
composition and desktop smoke checks reject excluded providers that reappear
through dependency resolution.

Desktop delivery shares one canonical OSTree repository, object store and signed
summary for both architectures. The normal publication workflow must import ARM
candidates into that shared repository; ARM publication refuses a repository
without its existing x86 desktop refs, and publication serializes shared summary
updates. Other channel/architecture refs are checked for changes afterward.

Architecture-specific metadata uses `luma/releases/aarch64/<version>` and
`luma/channels/aarch64/<channel>.json` inside that repository. ARM media and indexes
use `/media/aarch64/<channel>/`, and signed graphs use `/os/graph/aarch64/`.
Existing x86 metadata, media and graph paths stay unchanged. Graph signing accepts
`--arch`; sealed approved-refresh configurations may declare `architecture` and
`profile` (their defaults remain `x86_64` and `full`). A new sealed state and normal
publication approval are required before deploying any refresh configuration.

A native builder without release keys can prepare an OS export with
`scripts/os/export-ostree.sh --build-id ID --prepare-only`. This runs the normal
container import, candidate creation and image metadata/mode verification. It
writes `export-prepared.json` with an explicit unsigned result and hashes of the
build evidence. It does not write `export.env`, move a channel ref, sign anything
or qualify the image.

Transfer the build evidence named in that receipt and both recorded commits
from the native candidate repository to the authorized signing host. Record the
receipt SHA256, source revision and image ID through the authenticated builder
connection. Keep incoming evidence root owned and unwritable by other accounts.
The signing host runs `scripts/os/sign-prepared-export.sh --build-id ID
--prepared-repo REPO --receipt-sha256 SHA256 --source-revision COMMIT --image-id
IMAGE_ID`. It validates those identities, the current channel parent, actual
commit metadata/package inventory and the existing image metadata repairs
before the normal GPG signing and signed summary steps create `export.env`.
Release keys and passphrases remain on the authorized signing infrastructure.
Return the signed candidate to the native test host for installer, boot and
update qualification; signing alone does not authorize publication.
