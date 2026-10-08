<p align="center"><img src="website/public/brand/luma-wordmark.svg" alt="Luma" height="56"></p>

<p align="center">An operating system and everyday apps that work together.</p>

Luma is an operating system with a shared application platform, adaptive interfaces, and integrated desktop services. This repository contains the OS source, bundled apps, downstream patches, packaging, and device profiles.

[Website](https://simplyluma.com) · [Downloads](https://simplyluma.com/download) · [Build guide](BUILDING.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## Find your way

| Area | Source |
| --- | --- |
| Shared UI, semantics, application SDK and portals | [`src/luma-platform`](src/luma-platform) |
| Notes, Calendar, Tasks, Contacts, Messages, Phone and other shared apps | [`src/prairie-core`](src/prairie-core) |
| Tide, Leaf, Viewer, Monitor, Ari and system services | [`src`](src) |
| Charlie mail and Viola browser integration | [`src/external`](src/external) |
| Desktop shell and upstream integration | [`patches`](patches), [`extensions`](extensions) |
| Desktop, mobile and hardware configuration | [`config`](config) |
| OS composition and application packaging | [`image`](image), [`packaging`](packaging), [`scripts`](scripts) |
| Tests and fixtures | [`tests`](tests) |
| Application platform documentation | [`docs/developer-platform`](docs/developer-platform) |

The Creative collection, Office applications, and standalone Imager are separate products and are not included here. OS installation components remain in this repository.

## Platforms and devices

Desktop, tablet and mobile development share one platform and application tree. Installer images and boot requirements vary by architecture and device; a desktop ISO is not a universal phone installer.

| Target | Current source scope |
| --- | --- |
| x86_64 desktop | Fedora-based desktop composition and beta installer |
| AArch64 / ARM64 | Shared platform and mobile build profiles; component support varies |
| Mobile devices | Device-specific profiles and downstream hardware work; presence of a profile does not establish release support |

See [platforms and device profiles](docs/platforms.md) for navigation and support boundaries. Applications use GTK, libadwaita, standard Linux interfaces and portable packaging. Android compatibility is an optional isolated subsystem, based on Waydroid; host applications do not require it.

## Build and contribute

Start with [BUILDING.md](BUILDING.md). Components have individual build definitions; the complete OS also needs pinned upstream sources, package repositories and release infrastructure. This tree does not vendor a complete Chromium engine or a Fedora package mirror.

This is the initial public source baseline associated with build **20261008.8**. See [source baseline](docs/source-baseline.md) for provenance, publication changes and reproducibility limits. Beta status does not imply every integration or device is qualified.

## Licensing and credits

Original Luma code is Apache-2.0, documentation is CC-BY-4.0, and original media is CC-BY-SA-4.0 unless a file specifies otherwise. Third-party and derived material retains its own license and notices. See [LICENSE.md](LICENSE.md), [LICENSES](LICENSES), and [third-party sources](THIRD-PARTY-SOURCES.md).
