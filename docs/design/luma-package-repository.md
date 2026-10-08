# Why Luma packages cannot be replaced after a compose, and what to do about it

## The symptom

`rpm-ostree override replace <luma package rpm>` reports success, stages a
deployment, and silently does not apply the replacement. The only trace is a
line in a later transaction:

    Inactive base replacements:
      luma-backgrounds-0.2.0-1.luma.4.fc44.noarch

The same happens for `override remove`. It affects every Luma-built package —
`luma-backgrounds`, `luma-application-installer`, `luma-tide`,
`prairie-core-apps` — and no Fedora-built one. `gnome-shell` replaces fine.

Observed on the reference ThinkPad, 2026-09-11. Four separate deliverables were
blocked by it: the three wallpapers, the Depot console-command fix, the Photos
breakpoint fix, and the Calls producer.

## What it is not

Each of these was tested and eliminated:

- **Depsolve.** Dependency failures surface as explicit errors
  (`override remove --install` produced a real conflict message).
- **SELinux.** The working and failing RPMs carry identical labels
  (`unconfined_u:object_r:user_home_t:s0`) and no AVC denials are recorded.
  A `container_file_t` label on a test repository was corrected and made no
  difference.
- **Interference between overrides.** Reproduced with `override reset --all`
  first and a single package requested.
- **Version comparison.** `1.luma.3` → `1.luma.4` compares correctly.
- **A local rpm-md repository.** Building one with `createrepo_c` and using
  `override replace --experimental --from repo=` does not help either.

## What it is

rpm-ostree stores an *active* replacement as a pair — the new NEVRA and the
base NEVRA it replaces:

    base-local-replacements:  [[gnome-shell …36], [gnome-shell …28]]

It has to resolve the old side. The base commit records where it was composed
from:

    rpmostree.rpmmd-repos: [fedora, fedora-cisco-openh264, updates, updates-archive]

`gnome-shell` came from those repositories, so its base version is resolvable.
Luma packages never came from a repository at all: `provision-desktop-image.sh`
installs them with `rpm-ostree install /tmp/<nevra>.rpm` from local files, and
the provisioned tree is then exported as a new base commit. After that export
they are base packages with no provenance, so there is nothing to anchor a
replacement against and the request is recorded and dropped.

Supplying the *new* version from a local repository does not fix this, which is
why that experiment failed: the missing half is the *base* version's origin.

## The fix

Give the Luma packages a repository, at compose time, and install them from it.

1. After building the package set, generate rpm-md metadata over it with
   `createrepo_c` — the same set already listed in `config/desktop/packages.txt`.
2. Serve or copy that repository into the composing guest and write a
   `.repo` file for it.
3. Change `provision-desktop-image.sh` to install Luma packages **by name from
   that repository** rather than by local file path. The NEVRA pins in
   `config/desktop/inputs.env` stay exactly as they are and are what the
   install requests, so the composed set does not become less deterministic.
4. Ship the same `.repo` file in the image, pointing at wherever the repository
   is published for that channel.

The base commit then records the Luma repository alongside the Fedora ones, and
every Luma package becomes replaceable for the life of that image.

## What this is not permission to do

- It does not change what is installed. The pins still decide that.
- It does not relax signature policy. The repository carries the same packages
  the compose already installs; if a channel requires signed metadata, sign it.
  `gpgcheck=0` is acceptable only for a local development repository that never
  leaves the build host.
- It does not make the ThinkPad's current image replaceable. That image is
  already composed. Until a new image ships, changed Luma packages still need a
  full compose — which is why the three wallpapers were delivered per-user
  (`~/.local/share/backgrounds/luma` plus a matching
  `gnome-background-properties` file) rather than by package.

## Verification

The change is correct when, on an image composed this way:

    rpm-ostree status --json | jq '.deployments[0]."base-commit-meta"."rpmostree.rpmmd-repos"'

lists the Luma repository, and `rpm-ostree override replace` of a newer
Luma-built RPM appears in `base-local-replacements` rather than in
`Inactive base replacements`. Read the staged commit back with
`rpm-ostree db list <checksum>`; the client's exit code does not tell you.
