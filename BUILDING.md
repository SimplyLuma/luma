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

`src/external/viola/chromium-linux/appkit-spike/` contains the native host and Application Kit integration. The native35 package builder retains the separately built native30 browser engine. `scripts/packages/build-viola-browser.sh` requires `LUMA_VIOLA_ENGINE_ARCHIVE` to identify that archive and validates this exact SHA-256:

```text
2760bcadbb3864ffb637888b06e9717642c1add31656c2b81c6d0fb7fd5b1028
```

The current builder accepts x86_64 only. This repository is not a complete Chromium/browser-engine source checkout. The matching engine source, its upstream licenses, and its build recipe must be obtained separately; an arbitrary Chromium binary cannot substitute for the admitted archive. Do not infer an ARM Viola build from the shared native platform's AArch64 support.

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

## Handheld profiles

`config/mobile/` and `scripts/mobile/` contain device-specific native Linux integration. The Fairphone 6 rootfs entry point is `scripts/mobile/compose-fp6-rootfs.sh`, which requires root on a Fedora 44 AArch64 builder. It consumes the pinned shared packages plus handheld compositor, greeter, kernel, firmware, and device dependencies selected by the profile.

```sh
scripts/mobile/compose-fp6-rootfs.sh YOUR_OUTPUT_DIRECTORY
```

A rootfs archive alone is not a flashable universal mobile image. Bootloader, kernel, device tree, firmware redistribution terms, storage layout, and recovery procedures are device-specific. Inspect the corresponding profile and upstream source pins before building. Source availability and a successful userspace build do not establish current physical hardware support or safe installation on another device.

## Checks and contributions

Run component tests for the code you change, then the applicable package/source and image gates. Hardware-dependent checks require the named real environment; keep skipped or failed checks visible. Preserve third-party license texts, copyright notices, and patch author headers when rebasing. Separate app updates and OS releases must retain their own source/package identity and signature verification.
