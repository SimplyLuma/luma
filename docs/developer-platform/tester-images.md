# Luma Tester guest image contract

The launcher and guest OS have separate builds. The source includes a Linux
launcher, an x86_64 shared-image producer and portable/AppImage packagers.
There is no published guest feed. An existing installer or hardware rootfs must not be relabeled as a
qualified QEMU image. The older `prepare-fp6-twin.sh` starts from Fedora Cloud,
and `enable-fp6-twin-ui.sh` installs experimental Phosh. Those are useful lab
building blocks, not the complete current Luma Mobile guest.

## Producer requirements

Build one image for each target/architecture that is actually validated:

| Target | Architecture | Virtual platform | Suggested use |
| --- | --- | --- | --- |
| Mobile | x86_64 | q35 + virtio | Fast mobile shell/app iteration on x86 Linux |
| Mobile | aarch64 | virt + virtio + UEFI | ARM userspace and package validation |
| Desktop | x86_64 | q35 + virtio | Desktop preview |
| Desktop | aarch64 | virt + virtio + UEFI | ARM desktop preview when its packages exist |

Use the existing shared platform and app sources, package pins and native build
entry points in [BUILDING.md](../../BUILDING.md). Keep target-specific session
configuration in profiles. Device kernel, firmware and modem drivers should not
be installed to make a generic QEMU VM resemble a physical phone. Do not copy
personal guest disks or private release images into a public artifact.

The guest must include:

- A bootable standalone qcow2 with Linux virtio block, network, input and GPU
  drivers and a matching BIOS (x86 only) or UEFI bootloader. ARM must boot the QEMU
  `virt` machine, rather than a device-specific kernel/DTB.
- Real Luma shared UI, intended shell and apps, with recorded source/package build
  identity. Mobile needs handheld presentation/input capabilities, portrait output
  support and appropriate scaling. Desktop uses its normal desktop session.
- cloud-init enabled for the NoCloud datasource, OpenSSH server running, rsync,
  Python, a wheel/sudo-capable developer user, and a graphical login without a
  distributed fixed password. The seed creates that user and installs a unique
  per-session public key. This is a developer environment with passwordless guest
  sudo; never distribute it as a hardened production installation.
- A graphical user session that imports its display and D-Bus environment into
  the user systemd manager. App restarts use `systemd-run --user`, with no guessed
  socket paths. Authenticated `sudo systemctl --no-block poweroff` must work;
  mobile uses the ACPI power button for display sleep/wake.
- The tester readiness extension from `extensions/luma-tester`. After session
  startup and a completed compositor paint it writes
  `$XDG_RUNTIME_DIR/luma-tester/ready.json`, containing `target`, `boot_id`,
  `width`, and `height`. The host checks the current boot and requires portrait
  dimensions. Mobile additionally requires `renderer: luma-phosh`, its actual
  `/opt/luma/phosh/libexec/phosh` process and native first-frame marker at
  `/run/luma-display/home-ready`. An SSH port or a
  Wayland socket alone is insufficient. Use this image contract for `wait
  --graphical` and the GUI; no arbitrary readiness hooks run from manifests.

Before exporting, stop the image, remove personal accounts/keys/secrets and logs,
run `cloud-init clean --logs --machine-id` **inside the producer guest**, and shut
it down. This allows each tester seed to run on first boot. Do not run cleaning
commands on your host. Export a self-contained sparse disk (outside Git):

```sh
qemu-img convert -O qcow2 /path/to/stopped-build.qcow2 /path/to/luma-mobile-x86_64.qcow2
src/luma-tester/luma-tester manifest /path/to/luma-mobile-x86_64.qcow2 \
  --output /path/to/mobile.json --id mobile-x86-your-build \
  --build 'YOUR_ACTUAL_BUILD_ID' --target mobile --arch x86_64 --boot uefi
```

Use a lowercase id, e.g. `mobile-x86-20261017`. The manifest command records disk
size and SHA-256, and refuses backing files. It cannot establish the disk's guest
identity, architecture, provenance or cleanliness: those remain producer checks.
For a local image it emits `file:///…`; add `--url https://…/image.qcow2` when a
qualified artifact is available on authorized hosting. An image change requires a
new id. The launcher keeps existing writable workspaces on their original base.

## Qualification before distribution

Boot the exported image in a fresh tester state directory, verify the new SSH
key is the only developer access, and inspect guest build identity/package set.
Run the existing appropriate mobile or desktop smoke checks plus actual native
app tests. Confirm graphical first boot, pointer/keyboard interaction, portrait
rendering, app sync/restart, user systemd environment, pause/resume, screenshot,
graceful shutdown and persistent relaunch. Check native and TCG independently
when claiming both. Record real results with the exact image digest.

Existing FP6 checks named `mobile-twin` encode that old twin's exact reference
profile and synthetic state service. Do not claim they qualify a new profile
unless that profile intentionally implements those contracts. Device tests and
VM tests are separate evidence.

A future public feed should supply authenticated manifests and verified build
provenance with independently qualified downloads. This launcher currently reads
only explicitly selected local manifests; it performs no automatic updates or
publication. The Linux portable producer bundles QEMU. macOS HVF packaging,
Windows WHPX packaging and those host validations remain separate work.

## Source-owned guest producer

`scripts/tester/build-guest.py` composes the preview in a private QEMU builder
using the pinned Fedora Cloud base and the source-selected local Luma RPM cohort.
It builds the current shared apps from their normal RPM spec, including `%check`,
then applies the desktop profile. It never installs those
packages on the host or uses an existing personal VM disk.

The current producer exports x86_64 Desktop only. The earlier GNOME handheld
prototype was rejected as inaccurate and is no longer composed as Luma Mobile.
Mobile requires the actual phone Phosh/Phoc source and matching shared packages.
ARM needs its
own matching package cohort and a separately qualified producer path; selecting
an ARM argument does not convert x86 RPMs into an ARM image.

```sh
scripts/tester/build-guest.py --target desktop --build-id your-build-id \
  --base /path/to/verified/Fedora-Cloud-Base-Generic-44-1.7.x86_64.qcow2 \
  --rpm-pool /path/to/the/pinned/Luma/RPMs --output /path/to/private/output \
  --preflight
```

Remove `--preflight` to build, inside your normal admitted job with enough disk
and memory for a 3 GiB guest and its package transaction. The producer pins the
base hash in `config/tester/fedora-cloud.json`. Obtain the Fedora checksum file,
verify its signature with Fedora44's official public key, and verify the base
hash before supplying it. Builder SSH keys, writable disks and package logs stay
in the private output directory. Use a new output/build id for a different source.
`--resume` is restricted to the exact committed source/target/build identity.

A successful build exports `image.json`, a standalone qcow2, source/RPM receipt,
installed package list and the normal package build log. Builder authorization,
SSH host keys, instance identity and cloud-init state are cleaned before export.
This is a developer image with passwordless guest sudo and graphical autologin;
keep it separate from device installers and production OS releases. The build
receipt explicitly remains unqualified until fresh-session shell/app tests pass.


The current catalog contains `desktop.json`. A Mobile catalog entry must wait for
qualification of the actual phone renderer, Home/Library/Now surfaces and app
workflow. A portrait desktop is insufficient. Each image keeps a private writable
disk and developer key while sharing verified base files when their digests match.
The guest identifies itself as **Luma Developer Preview**, with its actual build id.

For a profile-only follow-up, `--parent-receipt /path/to/build-receipt.json` may
reuse a prior producer image. The producer checks the parent image digest, exact
RPM cohort, and unchanged shared app/platform source, then verifies installed
NEVRAs inside the new builder. It records the parent image and normal package-log
hash in the new receipt. Changes to app/platform source require a normal rebuild;
profile-only derivation cannot silently substitute untested app code.

## Mobile touch input

Mobile guests use QEMU's `virtio-multitouch-pci` in addition to keyboard/tablet
input. The viewer maps a primary press/drag/release to a real touch contact;
scroll gestures become touch movement. Losing focus releases the contact.
This uses the `input-send-event` multitouch API from
[QEMU 10.2](https://github.com/qemu/qemu/blob/v10.2.2/qapi/ui.json), including
tracking ID, slot, coordinates and touch-button state. The guest kernel must
include `virtio_input`; a normal mouse-only path does not exercise touch-only
Phosh gestures. Native shell gesture qualification is still required.
