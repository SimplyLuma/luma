# Ari 0.2.0: next Nightly handoff

The owner selected this version for the next scheduled Nightly alongside the
other accepted changes. No separate emergency OS release is requested. Feature
source is merged through [PR #92](https://github.com/SimplyLuma/luma/pull/92),
main merge `91e4a6d4eedf421d071280d6536cb674ff44449b`. Build from the converged
main revision selected by the Nightly owner, retaining these changes and the
matching packaging pins. Source readiness does not establish release acceptance.

## Coordinated October 30 build

The converged Android/Mobile/Ari source advances the shared native Platform/SDK
and sandbox runtime inputs to generation120. Ari's host remains generation21.
Earlier generation119 evidence below remains evidence for its actual inputs;
rebuild the changed platform cohort and use the resulting runtime for both CPUs.

## Deliver together

| Component | Required delivery |
| --- | --- |
| Ari engine, account support and application tools | OS-owned `luma-ari-0.1.0-1.luma.21.creator20261029.1.fc44.noarch`; bus `org.projectluma.Ari1` |
| Shared AppKit accessibility fixes | Native Platform and SDK generation 119, `1.luma.119.creator20261030.1`, on the actual target architecture |
| Toolkit used by sandboxed applications | Rebuild and sign `org.projectluma.Platform` and its matching SDK on runtime branch `44` with the same maintained platform sources |
| Ari interface | Signed `app/org.projectluma.Ari/ARCH/beta`, AppStream version `0.2.0`, through Depot |
| Tide owner interface | Signed `app/org.projectluma.Tide/ARCH/beta` containing `luma_tide/automation.py` and its application startup registration |

The shared-platform pin advances past published generation 118. Rebuilding the
previously selected generation 116 would cause a version regression and could
leave installed native apps using the old toolkit. Its spec, x86_64 and
aarch64 pins, SDK pin and desktop package list must stay consistent. Later
integration must preserve monotonic versions rather than restore an older
branch's pins. The shared icon spec and image pins also advance together to
`1.luma.43.creator20261030.1`, past published generation 42; this resolves the
pre-existing icon pin/spec mismatch found by the same Nightly preflight.
No icon artwork changes are part of this packaging correction.

Ari's Flatpak declares `X-Luma=min-host-ari=21`, matching
`packaging/flatpak/first-party-updates.json`. A Flatpak update cannot replace the
OS-owned engine. Keep the old deployment and explain the required OS update
when the host is missing or too old. Keep the existing app IDs, beta branch,
canonical remote, trust keys and user storage. Do not ship the Ari2 preview
archive, toolkit overlay, modified desktop helper or another update mechanism.

Tide's direct search/play/status interface belongs to the Tide application;
publishing Ari alone does not install it. The shared text-field accessibility
fixes belong in both the native platform and the sandbox runtime. An OS RPM
update alone does not replace a Flatpak's runtime. Preserve unrelated app and
runtime heads and retain rollback commits for both architectures.

## Source validation and reusable evidence

At production-packaging source `01bfb62c8` (the same tree as the feature merge):

- 203 Ari unit/protocol/policy tests, four production packaging tests and nine
  GTK regressions passed. Full Wayland UI ran 51 tests: 50 passed and one
  X11-only test skipped. AppStream validation passed for both Ari metadata files.
- Private native OAuth, Secret Service, model/effort selection, search request,
  remembered permissions, restart and sign-out checks passed. They use a test
  provider; they do not establish live subscription availability.
- Normal x86_64 Ari and Tide Flatpak builds passed AppStream, all three lint
  phases, permission extraction and OSTree integrity checks. These exports
  were unsigned and built against the then-published runtime, so they do not
  qualify the new generation-119 runtime or a signed installed update.
- The r11 actual Tide/GStreamer fixture verified decoded local audio, direct
  play, normal exit and cold launch without model calls or UI navigation.
  These are isolated server results, not remote-stream performance claims.

The Nightly handoff follow-up passed the public source checks, all 113
package-pin comparisons (with the existing Phosh exception), all 20 release-note
fragments, four Ari delivery tests, two update-registry tests and five package
preflight regressions. RPM version comparisons confirm both updated package
generations advance beyond their published baselines. These are source checks,
not installed release acceptance.

The [feature checkpoint](../../tasks/ari-parity-checkpoint-20261029.md) records
earlier component evidence and its limits. Keep actual producer revisions,
consumed-source hashes, architecture and results in private release evidence;
reuse an artifact only when its consumed inputs and qualification still match.
The source version change requires new platform packages and runtime exports.

## Nightly acceptance and publication

Use [release delivery](release-delivery.md) and the normal component producers,
OS gates, independently sealed publisher and networkless signing path.

1. Build the pinned Ari host and native platform packages. Build/sign the
   matching Platform/SDK runtimes, then build Ari and Tide against those signed
   prerequisites. Qualify each advertised architecture natively; do not relabel
   x86_64 output as aarch64. Retain the existing sandbox permission review.
2. Qualify the real signed app and OS combination: fresh install, an existing
   installation and an older retained baseline; actual launched Flatpak commit;
   model settings; conversation/sign-in/settings retention; app actions;
   restart; OS and app rollback. Verify that the native launcher cannot hide
   an obsolete sandboxed app. Keep network-unavailable and missing-host cases
   explicit failures rather than successful updates or “up to date.”
3. Include the admitted Ari/Tide refs and matching runtime in the offline app
   baseline and signed catalogue evidence. Update only the intended Ari listing
   in the current catalogue, using the 0.2.0 AppStream description, including
   connected-model data disclosure and optional sign-in. Preserve unrelated
   current listings; an old repository seed is not the current public catalogue.
4. Publish prerequisite objects and app media/deltas before exposing their
   signed refs, summaries and catalogue. Publish the accepted OS offer and
   matching installer through the ordinary Nightly procedure. Verify canonical
   HTTPS pulls with installed keys and default deltas, and confirm Depot offers
   the older installed Ari as an update after its OS prerequisite is present.

No production signing, publication or installed app-update/rollback result is
claimed by this handoff. Broader visual conformance and live hosted-account
acceptance remain incomplete; preview exceptions are not fabricated passing
release evidence. The next Nightly owner must record genuine acceptance before
offering the update to users.
