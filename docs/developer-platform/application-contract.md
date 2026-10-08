# Luma application contract 0.2

Luma applications remain ordinary Linux applications. The Luma contract does
not introduce a new archive, runtime, repository, or installer format. It
combines open, independently useful standards with stricter product rules:

- **Flatpak** provides cross-distribution transport, runtimes, sandboxing,
  updates, and rollback;
- **portals** mediate access to files, devices, notifications, and desktop
  services;
- **AppStream** and a freedesktop desktop file provide portable identity and
  presentation;
- **Luma UI and Semantics** provide the adaptive desktop/mobile and
  machine-readable interaction contracts;
- the **Luma SDK** checks identity, policy, lifecycle, provenance, and evidence.

The same Flatpak can run on another conforming Linux desktop. Luma metadata is
additive and namespaced; it must not make the application Luma-only.

## One identity

The reverse-DNS ID in `luma-app.toml` is canonical. The SDK rejects drift
between that ID, the Flatpak `id`, AppStream component and desktop
launchable IDs, desktop filename, icon filename and lookup key. The desktop command must
also match the Flatpak command. This avoids duplicate launchers, split
permissions, orphaned data, and misleading provenance.

## One adaptive application

Every Native candidate declares and tests pointer, touch, and keyboard input;
windowed and fullscreen-mobile presentations; and at least the 360, 500, and
1024 CSS-pixel width gates. Width selects layout. Presentation and input
capabilities select chrome and affordances. A phone-only or desktop-only fork
cannot pass this contract.

## Portal-first sandbox policy

Applications declare the portals they expect to use. The Flatpak manifest is
then inspected directly. The 0.2 Native-candidate policy rejects static
filesystem grants, unfiltered session/system buses, X11-only access,
all-device access, and wildcard D-Bus ownership. Narrow exceptions are not hidden in the app:
they require a future reviewed policy record and therefore do not earn Native
status through this initial checker.

Portal declarations explain intent; they do not grant access. The effective
Flatpak manifest and runtime portal grants remain the enforcement source.

## Legible removal and reset

The lifecycle declaration distinguishes application data, cache, and user
documents. Native candidates must support reset, removable cache, preserved
documents, and both **Keep data** and **Remove app data** uninstall choices.
The App & Data UI will present those choices from measured storage state, not
delete files merely because a manifest says they exist.

## Status is derived, never claimed

`luma lint` proves that a project is structurally coherent. It does not award a
store badge. `luma release-check` additionally requires:

- immutable source revisions and checksums for remote Flatpak sources;
- an ISO-dated evidence record bound to the same app ID and source revision;
- x86_64 and aarch64 results;
- desktop and mobile presentation results;
- accessibility, adaptive layout, keyboard, portal, reduced-motion, sandbox,
  semantic, uninstall, and update/rollback lanes;
- existing SBOM and machine-readable test-report artifacts.

The SDK derives `Development` or `Native candidate` from local evidence. A
trusted repository verifies the artifacts and signs the public **Native**
status; an editable project file can never mint that badge. Store review can
also derive **Supported**, **Compatibility**, and **Unverified** for software
that is useful but does not satisfy the full Native contract. Packaging alone
never earns Native.

## Generated project

```sh
luma new "Example" --id org.example.Example --destination example
cd example
luma lint luma-app.toml
luma inspect luma-app.toml
luma test luma-app.toml
```

The generated tree includes a JSON Flatpak manifest, AppStream metadata,
desktop file, replaceable vector starter icon, adaptive application, and an
intentionally failing Native evidence scaffold. The SDK accepts Flatpak's JSON,
YAML, and YML manifest forms; the starter uses JSON for deterministic generation.

Before release, CI replaces the false evidence gates with results from real
test lanes, emits the SBOM/report, pins network sources to immutable commits or
hashes, and runs:

```sh
luma release-check luma-app.toml
```

Changing booleans without independently verified build/test artifacts is not
review evidence and must be rejected by the publication service.
