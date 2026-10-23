# Tester: Hub handoff

## Virtual image catalog — 2026-10-23

Tester0.7.1 source accepts a proposed `tester_image` catalog kind alongside
existing `os_image` installer ISOs. This extension still needs Hub agreement and
published, qualified artifacts. The public catalog checked on October23 listed
only x86 desktop ISOs; the separate Mobile website download is a Fairphone
flashing package, not an instant-start VM disk.

A `tester_image` entry uses the existing HTTPS URL, exact byte length, SHA256,
architecture and unique artifact identity, with a `.qcow2` filename. Add
`tester_profile` containing the normal schema1 manifest fields, **without an
image definition**: `schema`, `id`, `target`, `arch`, `boot`, `cloud_init`, `user`
and `build`. The client binds that profile to the catalog artifact's actual
URL/size/checksum, verifies a standalone unencrypted disk and registers the
matching Mobile or Desktop experience. Installer ISOs remain Desktop media;
phone sparse images and boot binaries are never offered as QEMU installation
media.

Optional Mobile profile fields are `display: {width, height, scale}` and
`capabilities: {home, dynamic_display}`. These describe verified guest support,
not host CPU policy. For example, a fixed portrait image can declare864×1920
at scale2, with both controls false, while a newer image enables those controls
after its guest helpers are qualified. The GUI does not issue unsupported
control commands. Existing profiles keep their prior behavior when omitted.

The Mobile system's source identity must remain separate from its virtual boot
adapter. The public ARM payload20261015.21 was checksum-verified and its genuine
Phosh Home, SSH and graceful shutdown were observed in a private M4/HVF VM with
a generic ARM boot adapter. This does not qualify public VM artifacts or permit
attributing that derivative to an existing desktop ISO ID. Keep the common
userspace plus device/virtual adapter model described in [Tester images](tester-images.md).

Desktop ISO, Desktop VM and Mobile VM entries need distinct artifact identities
even when channel/build/architecture match. The Hub owns that namespace and its
publication policy. Do not invent production IDs or URLs. Ticket creation and
completion use the selected catalog artifact; cache reuse creates no ticket.

## Earlier verified ticket integration — 2026-10-22

The Hub ticket client is implemented in Tester 0.2.0, source `8a8bee7d`.
A private Linux x86_64 preview has been built and delivered to the Dell.
This is not a public release, signed index or macOS/Windows qualification.

## Verified integration

- Full live HTTPS download of `os/nightly/20261021.1/x86_64`:
  7,198,212,096 bytes, exact size and SHA-256 verified, then completion reported.
- The first attempt's real CDN403 failure and subsequent successful transfer
  retained the same ticket. A normal `Luma-Tester/0.2.0` User-Agent is required;
  the CDN rejects the generic Python urllib identifier with Cloudflare1010.
- Repeated completion returned `counted: false`. Cached reuse made no ticket.
- A separate deliberate network failure was reported and remained incomplete.
- Every live request used `source: test`. Public totals were not exercised.
- Native ARM-host selection was simulated in the real LumaUI workspace: missing
  ARM images show the ARM64-coming notice and **Start with Emulation**. Adding an
  ARM profile selects it automatically. No ARM VM/hardware test is claimed.
- Sixty component checks, native workspace/QEMU controls, actual AppImage entry,
  and packaged genuine Mobile touch/keyboard/Home/app-hotfix workflows passed.
  Mobile workflow qualification used software graphics on the build server.

Private job evidence is retained in the Tester task record. Ticket IDs are
anonymous reporting capabilities and are deliberately omitted from this document.

## Ready for the Hub

The client uses the published contract without accounts or cookies. Source and
private previews default to `test`; `build-portable.py --release` changes only
the staged package to `tester`. Production packaging still needs the normal
release qualification/signing/publication lane. Do not publish the private
test-attribution preview as the production release.

`catalog` and `download` support installer images and Tester app artifacts. A
published developer-disk manifest can associate an exact matching artifact ID
with its immutable image; the normal GUI session preparation then uses tickets.
Resume, verification, failure reporting and offline analytics fallback are
implemented. Details and commands: [Tester download attribution](tester-downloads.md).

## Next catalog agreement

Today's public OS artifacts are installer ISOs. Tester's instant-start Desktop
and Mobile sessions use separately qualified, standalone cloud-init qcow2 disks.
The private disks must not be attributed to public ISO artifact IDs.

To make the lightweight app fetch those sessions from the Hub, publish signed
developer-disk entries that identify both experience and architecture and carry
the existing trusted Tester manifest metadata. Desktop and Mobile need distinct
artifact identities even when channel, build and architecture match. The current
`os/<channel>/<build>/<arch>` identity cannot identify an ISO plus both developer
disks simultaneously. Agree an extended artifact-ID/catalog shape before wiring
those new identities into the manifest validator; do not synthesize IDs or URLs.

Tester app artifacts additionally need the integrator's signed
`tester/latest.json` and uploaded release files. Automatic update prompts,
in-place executable replacement and ISO installation are separate work; the
ticket client does not claim to implement them.

### Ready-to-run images and installer media

The default Try/Developer path needs a preinstalled, standalone VM disk, with
its target architecture, boot profile, checksums and per-session provisioning.
An installer ISO starts an installation and must remain an explicitly labelled
installation-media choice. Renaming or converting an ISO does not perform the
installation. Tester labels these separately and uses Start Installer for ISOs.

Publish prepared `tester_image` artifacts alongside the existing website/Imager
installation media. Keep a common OS build identity while distinguishing each
virtual disk from physical-device media and its boot adapter. The client-side
catalog support does not itself publish these artifacts; until the Hub's release
publisher supplies the new entries, prepared private profiles remain local
preview inputs rather than an automatic public download feed.
