# High-impact Mod pilot fixtures

These manifests and host tuples are deterministic planning fixtures. They are
not part of the built-in catalog and contain no executable payload. Their
purpose is to force complete Experience and Hardware evidence checklists before
any signed release or privileged transaction is possible.

- `org.kde.plasma.luma-experience.candidate.mod.json` enumerates the complete
  mutually exclusive KDE session-provider contract while retaining the shared
  `prairie-core-apps` capability. Its host fixture identifies Prairie as the
  active provider, and its manifest explicitly plans nine coordinated provider
  transitions. This is planning evidence only: it does not install KDE or
  change the running session.
- `org.projectluma.pilot.hardware.lenovo-20th003hus.candidate.mod.json` binds the
  first hardware qualification exercise to the lab machine's observed DMI
  identity: Lenovo machine type `20TH003HUS`, ThinkPad P1 Gen 3. It claims only
  qualification, not hardware support that has not been measured.
- `org.projectluma.pilot.hardware.microsoft-surface-pro-9.candidate.mod.json`
  establishes the first model-scoped Surface qualification contract. It is
  intentionally payload-free and excluded from the catalog until physical
  stock-kernel and missing-function lanes pass the complete evidence gate.
- `org.projectluma.pilot.hardware.apple-t2-macbookpro16-1.candidate.mod.json`
  reserves one exact Intel T2 Mac model for qualification. It intentionally
  ships no T2 kernel, firmware, boot argument, or support claim.
- `org.projectluma.pilot.hardware.oneplus-cph2653.candidate.mod.json` is an
  image-compose-only phone-port contract for the exact OnePlus 13 CPH2653
  product identity. It retains the shared core-app capability and explicitly
  does not claim that the device boots or that modem/camera/power works.

Generate evidence templates with:

```sh
PYTHONPATH=src/luma-mods python3 -m luma_mods.author pilot-template \
  examples/mods/pilots/org.kde.plasma.luma-experience.candidate.mod.json \
  --host examples/mods/pilots/host-kde-desktop.json \
  --output kde-pilot-evidence.json
```

A pilot cannot set `release_ready: true` until every required check is `pass`
and carries a SHA-256 identity for retained evidence.

The image composer can consume the same resolver without inventing a second
device-port format:

```sh
PYTHONPATH=src/luma-mods python3 -m luma_mods.author image-plan \
  org.projectluma.pilot.hardware.oneplus-cph2653 \
  --catalog examples/mods/pilots \
  --host examples/mods/pilots/host-oneplus-cph2653-image.json \
  --output oneplus-cph2653.review.lock.json
```

That candidate output is deliberately review-only. The production composer
must refuse it until catalog acceptance and publisher verification authorize
the exact lock.
