# Updater architecture and device admission

- Owner: luma-dev-2 / Codex arm_gate_ssd_r4
- Status: ready for integration; source tests passed; package and device images not built
- Branch: fix/updater-architecture-device
- Base: 94f9e9827c2b17e23738c4b442ac42c53c979951

Selective maintainer updater port, retaining newer shared desktop behavior and
all existing signing key/container/lock boundaries. The shared updater admits
FP6 and Titan using actual device-tree board identity, exact signed graph target,
and immutable commit architecture. Generic older commits with no architecture
key retain compatibility; explicit wrong/malformed architecture and metadata
read failures clean up the staged generic update.

New desktop compositions use architecture-qualified graph URLs. Signing keeps
the older x86 alias, and phone graphs remain in their own architecture/slug
namespace. The two phone policy files explicitly set target, ref template and
graph URL; their owning image/package producers must install those exact files.

The shared noarch package source/pin advances to luma-update-1.0.0-1.luma.20.fc44.
Its existing builder includes hardware.py through its normal Python module glob.
No package, signature, image, kernel, device update or publication was produced.

Validation actually ran:

- Updater suite: 327 tests, OK, one existing missing documentation-table skip.
- Routing/validator/desktop suites: 30 tests, all pass.
- Native immutable OSTree tests use real GI repository commits, including
  typed malformed metadata; no production sysroot or signing key is used.
- Four-profile policy fixture executes the maintained desktop compose block
  with only its output redirected, loads actual phone overrides, then verifies
  exact signed graph selection for x86 desktop, ARM preview, FP6 and Titan.
- Shell syntax and git diff whitespace checks pass.

Integration: build the shared noarch package from the integrated source, retain
its source/SRPM/RPM hashes, update image inputs with that actual artifact, and
let each device owner qualify their image. These source fixtures do not prove
installation/update success on any physical device. The independent R20 engine
compiler/cache remains running and unchanged.

2026-10-24: retained official graph settings now resolve by verified architecture and the configured device table slug without rewriting administrator files. Explicit incompatible official buckets and phone commit metadata on generic desktops refuse; third-party/test URLs remain unchanged. Publisher/client table parity and required-label policy inputs are checked. Preserved the existing mobile retry/network/notifier/trial-health behavior through selective convergence. Shared release23 exceeds private22; spec and maintained package builder require native OSTree/GI/OpenSSL checks. Desktop input/package pins agree. Actual client suite348 tests passed (two explicit skips: unavailable public D-Bus contract document and physical FP6 helper environment); graph/namespace/profile suite33 passed. These are source/configuration tests, not four composed images or phone deployment qualification. Package build remains pending.

2026-10-24: maintained Fedora package producer completed shared noarch release23 from commit37201d984. Actual standalone RPM suite347 tests passed with eight explicit skips: six full-repository Depot adapter tests, the external contract document and the physical FP6 query. All four native OSTree metadata tests ran. The unchanged four-profile compose integration now lives with full-repository tests and passed separately; it is not part of the standalone source archive. Independent RPM extraction verified hardware.py and shipped default graph configuration match source, and both updater/remote NEVRAs are23. No installation, image composition or publication is implied. Current phone producer integration and qualified graph publication remain separate owner gates.
