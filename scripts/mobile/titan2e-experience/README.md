# Titan Mobile experience hotfix source

`../prepare-titan2e-experience.py` combines the exact Titan Phosh 83.1 source RPM
with the merged Mobile patches 0044–0051 and the shared Home widget library.
It preserves the shipped phone's output sizing, fold/pocket handling, audio,
gesture and cached-shadow changes. It rejects a different input RPM, unexpected
patch results or an existing output directory.

The result includes the complete visible app catalog on paged Home, the dock and
status changes, radio capability visibility, and shared native Weather/Calendar
widgets. The compact-card adapter preserves the phone cohort's sizing API.
This assembles source; it does not flash a phone or publish a release.

```sh
python3 scripts/mobile/prepare-titan2e-experience.py --srpm EXACT_TITAN_SOURCE_RPM --output NEW_DIRECTORY
```

Build `home-widgets` first with Meson on an ARM Fedora 44 builder, run its tests,
and use its `meson-uninstalled` pkg-config directory when building the assembled
Phosh source. Install both under one private prefix with `libdir=lib64`, keeping
the packaged shell for rollback. The running user service needs that prefix's
library and schema directories. Check dependency resolution and the actual
running executable after switching the shell.

Source RPMs, generated binaries, firmware, device keys and private device evidence
are operator inputs/outputs and must remain outside Git. Personal-device delivery
does not establish release signing or general hardware qualification.
