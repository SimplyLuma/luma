# Tester download attribution

Tester 0.2.0 integrates the anonymous Hub download-ticket API at
`https://hub.simplyluma.com/api/hub/downloads/`. Source checkouts and private
previews send `source: test`, which the Hub excludes from public totals.
`build-portable.py --release` changes only the staged package to `source: tester`;
it does not publish, sign or upload anything.

## Download flow

`luma-tester catalog --kind os_image --channel nightly` lists catalog artifacts
without creating tickets. The native host architecture sorts first. On ARM hosts
without an ARM image, the catalog and workspace explain that ARM64 is coming and
offer x86_64 under emulation. ARM entries become preferred when available.
Mobile continues to prefer its qualified ARM profile where one exists; selecting
an architecture never converts a desktop profile into a phone profile.

Download an exact catalog artifact with:

```sh
luma-tester download os/nightly/BUILD_ID/x86_64 --output ./luma.iso \
  --experience desktop --purpose first_download
```

Take `artifact_id` from the catalog output; the example's `BUILD_ID` is a
placeholder. This command downloads a file. It does not install or boot an ISO.
Use `--purpose update` for a newer cached build or `explicit_redownload` for a
deliberate fresh download to another destination.

For a published developer disk, associate its exact Hub `artifact_id` with the
trusted Tester manifest using `manifest --artifact-id`. Normal GUI/CLI session
preparation then uses the same downloader. The ticket's size and digest must
match the manifest before its download URL is accepted. Private/local disks
and cached launches make no ticket. Session preparation still requires a
standalone, unencrypted cloud-init qcow2; installer ISOs are not session disks.
The current public catalog offers installer ISOs, not the private qualified
Mobile/Desktop developer disks. Do not attach an ISO's artifact ID to those disks.

The downloader retains `<output>.part` and `<output>.download.json` on interrupted
transfers, reuses the ticket on retry, and validates HTTP range responses. If the
server ignores Range it restarts the bytes using the same ticket. SHA-256
mismatches are reported and the bad partial is removed. Only a complete file
matching both the expected size and SHA-256 sends `/complete`. Existing cached
files are verified locally without creating or completing another ticket.

Ticket creation/reporting are best effort with bounded timeouts. A failed Hub
call, expired ticket or unavailable analytics never blocks a verified file's use.
When ticket creation fails, the catalog/manifest URL, size and digest remain the
fallback. Verification failures still stop the download. Local ticket metadata
contains an anonymous per-download ID; do not include it in public bug reports.

Only the documented fields are sent: artifact, source, purpose, experience,
Tester version, host OS and host architecture. No project paths, machine IDs,
account identifiers or credentials are sent. The client does not use cookies.

## App updates and remaining work

`catalog --kind tester_app` and `download tester/VERSION/PLATFORM --purpose update`
use the same implementation. The release index must be published and signed by
the authorized integrator before these artifacts exist. Downloading an update
does not replace the running executable or migrate workspaces.

Automatic update checks, native update prompts, ISO installation and a published
ready-to-run developer-disk feed are not implemented by this integration.
The existing offline/sidecar previews remain usable without Hub access.

## Validation

Run the component checks under normal resource admission:

```sh
PYTHONPATH=src/luma-tester python3 -m unittest discover \
  -s src/luma-tester/tests -p 'test_*.py'
```

Live qualification must use `source: test`: create a ticket, actually download
and verify the full artifact, report completion, check idempotence, and exercise
a separate forced failed transfer. Never submit production attribution during QA.
