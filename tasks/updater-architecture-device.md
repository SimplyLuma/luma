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
