# Tester 0.2.0: Hub handoff — 2026-10-22

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
