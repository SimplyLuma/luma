# Luma IMS daemon provenance

`src/imsd` is Project Luma's canonical, reproducible source for the native
Linux IMS/VoLTE service used on the Fairphone 6.

- Upstream: <https://forgejo.catcrafts.net/Catcrafts/imsd>
- Imported commit: `547361a4d476014adccf66170adea19d762bc9e8` (tag `v1.1.0`)
- Upstream version: `1.1.0`
- Previous import: `9267c02c9f1eb501baef41b6c1e5fe4391606f72` (`0.3.0`)
- License: `GPL-3.0-only`

Luma's downstream changes add carrier-interoperability behavior, resilient
registration and call recovery, protected resume state, audio-routing hooks,
and the RFC 3840/GSMA IR.92 voice capability declarations needed by the FP6
physical gate. Upstream removed its Makefile in 0.3.10; Luma keeps it because
Fedora does not package crafter-build, so it must track `project.cpp`.

Downstream behaviour that differs from upstream 1.1.0:

- The reg-event SUBSCRIBE is opt-in (`REG_EVENT=1`): T-Mobile US answers it
  500 and closes the protected TCP flow.
- `DialEmergency`, `HangUpEmergency` and `GetEmergencyCallState` bypass the
  polkit gate; the bus policy limits their callers and the daemon validates
  number and call provenance.
- Media helpers drop to the audio account in-process instead of through
  `sudo`, run in their own process group, and use the `Phone` media role.
- The protected Contact carries `transport=tcp` on a TCP registration.

The Luma service, bearer setup, and state guard remain in
`scripts/mobile`; the RPM intentionally installs those hardened integration
files instead of upstream's generic service files.

The historical working tree under `build/mobile/fp6-ims/imsd-source` is a lab
artifact, not a source input. Builds and device images must consume this
directory exclusively.
