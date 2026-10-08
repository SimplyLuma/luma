# Luma IMS daemon provenance

`src/imsd` is Project Luma's canonical, reproducible source for the native
Linux IMS/VoLTE service used on the Fairphone 6.

- Upstream: <https://forgejo.catcrafts.net/Catcrafts/imsd>
- Imported commit: `9267c02c9f1eb501baef41b6c1e5fe4391606f72`
- Upstream version: `0.3.0`
- License: `GPL-3.0-only`

Luma's downstream changes add carrier-interoperability behavior, resilient
registration and call recovery, protected resume state, audio-routing hooks,
and the RFC 3840/GSMA IR.92 voice capability declarations needed by the FP6
physical gate. The Luma service, bearer setup, and state guard remain in
`scripts/mobile`; the RPM intentionally installs those hardened integration
files instead of upstream's generic service files.

The historical working tree under `build/mobile/fp6-ims/imsd-source` is a lab
artifact, not a source input. Builds and device images must consume this
directory exclusively.
