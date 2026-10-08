# Luma Mods foundation

This source tree contains the first production-shaped foundation of the Luma
Mods contract. It
strictly inspects bounded JSON manifests, calculates impact and activation from
declared effects, resolves explicit Mod dependencies, checks host compatibility
and semantic capabilities, and emits a deterministic plan identity. It also
contains:

- fail-closed offline Sigstore publisher verification;
- TUF-backed catalog refresh and verified artifact acquisition;
- a keyless, deterministic offline-TUF ceremony request with explicit role
  thresholds, key separation, target-role assignment, and custody checks;
- a conservative automatic-update envelope that holds every broader or
  unverifiable candidate for fresh review;
- a read-only host inventory that labels observations unmanaged or unknown;
- crash-safe installed state with locking, generation checks, atomic writes,
  transaction journals, audit history, and recovery snapshots;
- a declarative user-preference profile backend supporting install, compatible
  update, disable, re-enable, remove, and exact restoration; and
- an adaptive Mods gallery and review sheet with the complete bounded
  install/update/disable/enable/remove lifecycle;
- deterministic maintainer tooling for init, lint, plan, review evidence,
  bounded payload identities, rootless reproducible sandbox builds, complete
  high-impact pilot evidence, and unsigned catalog-signing handoff; and
- deterministic image-composition locks that bind a resolved Mod graph to one
  base, architecture, presentation, hardware, and kernel tuple without
  executing it; and
- a data-only, polkit-gated system boundary whose rpm-ostree adapter stays
  closed until recovery readiness is independently proven. The boundary now
  journals known-good/candidate deployments before activation and packages
  fixed boot observation, health promotion, and watchdog rollback units.

The preference CLI and graphical review surface are functional. The lifecycle API
cannot run commands or write arbitrary files; it accepts only registered
Luma-owned setting domains. The privileged D-Bus service is packaged but cannot
stage or activate anything without two root-owned recovery gates that Luma does
not ship. No manifest or system request accepts a hook or shell-command field.

Experience planning also records the active provider behind each known session
capability. Replacing a shell, compositor, locker, notification service, or
other exclusive/selectable provider requires an explicit compatible
`replaces` declaration and produces a visible old-to-new transition. A plan
cannot silently swap providers, and resolving one never alters the running
session.

The public term is **Mod** for every catalog item. The internal `kind` remains
mandatory so Luma can distinguish applications, appearance and behavior
changes, capabilities, experiences, hardware support, developer environments,
compatibility runtimes, and core-system changes.

## Local proof

```sh
PYTHONPATH=src/luma-mods \
  python3 -m luma_mods.cli inspect \
  examples/mods/org.projectluma.mod.green-dock.mod.json \
  --trust-policy examples/mods/trust-policy.json

PYTHONPATH=src/luma-mods \
  python3 -m luma_mods.cli plan org.projectluma.mod.green-dock \
  --catalog examples/mods \
  --host examples/mods/host-desktop.json \
  --trust-policy examples/mods/trust-policy.json

PYTHONPATH=src/luma-mods \
  python3 -m luma_mods.review \
  examples/mods/org.projectluma.mod.green-dock.mod.json \
  --catalog examples/mods \
  --host examples/mods/host-desktop.json \
  --trust-policy examples/mods/trust-policy.json

PYTHONPATH=src/luma-mods \
  python3 -m luma_mods.cli host-scan --json

# Production-shaped installs resolve again from current TUF metadata in the
# same process that creates installation authority. Serialized plans never
# retain that authority.
luma-mod catalog-plan org.example.mod \
  --bootstrap-root /usr/share/luma/mods/trust/root.json \
  --cache "$XDG_CACHE_HOME/luma/mods/catalog" \
  --metadata-url https://mods.example/metadata/ \
  --targets-url https://mods.example/targets/ \
  --host host.json --trust-policy publisher-policy.json

luma-mod catalog-install org.example.mod \
  --bootstrap-root /usr/share/luma/mods/trust/root.json \
  --cache "$XDG_CACHE_HOME/luma/mods/catalog" \
  --metadata-url https://mods.example/metadata/ \
  --targets-url https://mods.example/targets/ \
  --host host.json --trust-policy publisher-policy.json

# The graphical Gallery uses the same path when explicitly given a release
# configuration. Luma does not ship a placeholder production root or URL.
luma-mods --catalog-config /usr/share/luma/mods/trust/catalog-client.json

PYTHONPATH=src/luma-mods \
  LUMA_MOD_CATALOG="$PWD/src/luma-mods/catalog" \
  python3 -m luma_mods.gallery

PYTHONPATH=src/luma-mods \
  python3 -m luma_mods.author lint \
  src/luma-mods/catalog/manifests/org.projectluma.mod.green-dock.mod.json \
  --profile src/luma-mods/catalog/profiles/org.projectluma.mod.green-dock.profile.json

PYTHONPATH=src/luma-mods \
  python3 -m luma_mods.author pilot-template \
  examples/mods/pilots/org.kde.plasma.luma-experience.candidate.mod.json \
  --host examples/mods/pilots/host-kde-desktop.json \
  --output /tmp/kde-pilot-evidence.json

PYTHONPATH=src/luma-mods \
  python3 -m luma_mods.author image-plan \
  org.projectluma.pilot.hardware.oneplus-cph2653 \
  --catalog examples/mods/pilots \
  --host examples/mods/pilots/host-oneplus-cph2653-image.json \
  --output /tmp/oneplus-cph2653.review.lock.json

# A release operator first assembles unsigned targets, then creates a request
# for the separately controlled standard TUF signer. No private key enters the
# source/build environment.
luma-mod-catalog-ceremony POLICY.json UNSIGNED-HANDOFF/ REQUEST.json
```

The dependency is shown with its maintainer-supplied reason and whether it is
already installed. The resolver also independently verifies versions,
capabilities, compatibility, conflicts, and effect-derived impact.

For preference Mods, the reviewed profile is a content-addressed evidence
target in the same delegated role. The client acquires it through python-tuf,
matches it to the manifest's `evidence.source_digest`, validates its Mod and
version identity, and refreshes the complete plan again immediately before a
graphical or command-line write. If the catalog composition or profile changed
after review, installation stops and asks for a new review.

The user-facing discovery, review, dependency, verification, update, removal,
developer, and hardware experience is defined in
`docs/research/luma-mod-gallery-product-contract.md`.

## Lifecycle safety contract

- Local/Unverified Mods require a distinct explicit authorization record.
- A verified publisher label cannot authorize installation by itself. The
  exact manifest must also come from a live TUF refresh rooted in the
  image-pinned catalog key, and its delegated catalog role must match the
  protected publisher policy.
- The client recomputes the content-addressed catalog snapshot ID, enforces
  role-specific target namespaces, and refuses target reuse across entries.
- Profile values must exactly equal the target's declared setting domains.
- Verified preference profile bytes must match the source digest recorded in
  the admitted manifest.
- Dependencies cannot be removed while referenced.
- Compatible updates cannot silently add dependencies or broaden effects.
- Disable/remove restore the exact pre-install value, including absence.
- An interrupted backend operation is recovered before any new transaction.
- A hardened user-session oneshot reconciles any journal left by a hard process
  interruption before the graphical session starts.
- State and cache roots reject symlinks; files are bounded and replaced
  atomically after fsync.
- System requests contain typed artifacts/effects only—never commands, hooks,
  or publisher-selected destinations.
- Graphical clients cannot author those system requests. They submit only the
  Mod ID plus the signed catalog snapshot and composition identities displayed
  during review. The root service refreshes the protected TUF catalog,
  reconstructs the host tuple from immutable Luma policy and kernel/DMI
  sources, verifies every publisher, resolves the graph, and builds the typed
  request itself.
- System payloads enter the root-owned content-addressed store only after TUF
  verification and a second bounded size/SHA-256 pass while copying with
  symlink following disabled. The rpm-ostree backend consumes only those exact
  stored bytes.
- System staging durably binds the reviewed composition, booted known-good
  checksum, staged candidate checksum, attempt limit, and outcome before reboot.
- An unpromoted candidate reaches a fixed rpm-ostree rollback path; promotion
  is possible only after boot observation and graphical health.
- Kernel-facing Mods require exact positive hardware and kernel identities.
  Luma Verified hardware publishers additionally require a retained previous
  composition and an explicit health gate; Community content cannot stage
  kernel, initramfs, boot, or Secure Boot effects.
- An image-plan lock is review-only until accepted TUF target evidence binds
  every manifest and payload and the publisher trust policy passes.
- Reloading serialized lock JSON never restores install authority; the trusted
  composer must re-run TUF and publisher verification in-process.

## Deliberate boundaries

- JSON is the deterministic spike serialization, not an approved offline
  bundle or file-association format.
- Detached Sigstore bundles can be checked offline against a protected
  publisher policy. The example remains honestly unsigned and therefore
  displays as Local / Unverified.
- The TUF client, deterministic unsigned target assembler, keyless ceremony
  request, and signed-staging acceptance receipt are implemented. Luma has not
  generated production keys or published a signed release. Build tooling never
  receives production signing keys.
- Green Dock uses the finite `org.luma.shell.dock.appearance` contract. The
  downstream Prairie dock maps `green-glow` to compiled styling and monitors
  atomic preference updates. No Mod-provided CSS is evaluated.
- Green Dock is honestly desktop/tablet-only until the handheld presentation
  ships an equivalent consumer. The engine and noarch `luma-mods` package are
  still identical in both images.
- System RPM/OSTree, service, boot, firmware, and kernel mutation remains
  disabled. The D-Bus service and fixed-argv adapter are present for testing,
  but activation requires root-owned recovery gates, a root-owned catalog
  configuration, and a root-owned system-host profile that the package never
  creates. No placeholder trust or host identity is treated as production.
- A production TUF root/threshold ceremony, actual Hardware support payload,
  and complete KDE session evidence remain release gates. The physical
  automatic-rollback drill has passed on the lab VM. Deterministic fixtures and
  mandatory evidence checklists include an exact Surface Pro 9 qualification
  candidate; none of the high-impact pilots is catalog-shipped.
