# Tester image delivery and application updates

Release preparation decision, October 27, 2026. This is a proposed delivery
contract; it does not assert that the Hub has published these artifacts or that
automatic app updates are implemented.

## Two OS images

Publish one normal Luma ISO for x86-64 and one for ARM64. Desktop, Tablet and
Mobile select a presentation of that OS; they do not require separate downloads.
Keep architecture selection explicit, with the current host-native defaults for
Desktop/Tablet and ARM64 for Mobile. Changing presentation must reuse the cached
ISO. Never substitute a physical phone flash payload for a generic ARM VM ISO.

The Tester application contains the launcher, QEMU and its dependencies, not the
large OS images. It reads the existing Hub catalog, shows compatible releases in
Images, and downloads the selected immutable artifact on demand. Retain local
imports and an offline catalog cache. Use the existing download progress, resume,
size/SHA-256 verification and `source: tester` attribution described in
[tester-downloads.md](tester-downloads.md). Qualification uses `source: test`.

Use the same artifact IDs and bodies as the website, such as
`os/nightly/BUILD_ID/x86_64` and `os/nightly/BUILD_ID/aarch64`. The Hub's source
field distinguishes Tester downloads from website downloads. Do not duplicate
the bodies merely to count downloads. Every catalog item must provide its exact
size, SHA-256, architecture, channel, version and immutable download URL.

An installer ISO remains installation media. The catalog must not call it an
instant desktop, and a phone image is not an alternative generic boot image.
Shared live-session support belongs in the normal OS image. A future prepared
VM disk may be an optional download if necessary, with its own artifact kind,
verified boot profile and provenance; it is not a third required OS ISO.

## Updates preserve workspaces

Refreshing the catalog does not download gigabytes or update a running guest.
Offer a newer compatible ISO with its version and size; download only after the
person chooses it. Keep Installed and available releases distinguishable.

Cache images by content digest. New bytes must never replace the backing image
of an existing workspace. Keep its original manifest, writable disk, firmware
variables, snapshots and SSH identity together. Offer a new workspace for a new
ISO; upgrading an installed guest uses Luma's normal updater. Removing an old
cached image must first check every workspace and snapshot that depends on it.

## Tester app releases are independent

For the first public release, publish versioned AppImage, DMG and Windows
packages through the normal authorized release infrastructure and expose them
as `tester_app` artifacts. Existing catalog/download commands already support
that kind. Shipping a new OS ISO must not require rebuilding the Tester app.

The smallest useful app update UX is a bounded background version check and
“Luma Tester VERSION is available” with Download and Later. Show it once per
version, outside an active interaction with the VM. Offline or failed checks
leave the app usable. Choose the package by host OS and host architecture,
independently of the architecture currently being emulated.

Authenticate update metadata and native packages through the release trust
infrastructure. A checksum from the same unauthenticated index is insufficient
to authorize replacement of an executable. Preserve Apple signing/notarization
and Windows signing requirements; QA packages are not public release packages.

Initially download the verified package and reveal it for installation. This
avoids an updater service and permits the person to close/save their guests
first. Do not replace a running executable, force a restart, migrate workspace
data implicitly, or run arbitrary catalog-provided commands. A later native
self-updater can use this same release channel after its signature, rollback
and active-VM behavior have been qualified.

## Publication gate

Before pointing the public catalog at an artifact:

- Record the exact source revision and native component receipts for each CPU.
- Verify the complete uploaded body through the actual download path, not HEAD.
- Boot each ISO on its native CPU; test all three presentations after setup.
- Exercise an interrupted download and retry, cached offline launch, and a
  previous workspace with snapshots after a catalog refresh.
- Publish only qualified, correctly signed application packages. The current
  private Windows package still needs native qualification; the private Mac
  package is ad-hoc signed and not notarized.

The private October 27 media assembly uses reviewed OS source
`eccd6bb20f2b2fd0afb784ea868621aabc09ca75` (PRs 76–78; installer changes excluded).
It reuses unchanged, verified native installer runtimes and consumes normally
signed OS exports. A successful assembly receipt proves media construction;
guest boot and public release acceptance remain separate evidence.
