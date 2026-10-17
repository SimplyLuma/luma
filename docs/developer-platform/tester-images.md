# Luma Tester guest image contract

The launcher and guest OS have separate builds. This first source milestone
provides the launcher; it does not publish a Luma Mobile/desktop image or image
feed. An existing installer or hardware rootfs must not be relabeled as a
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
  socket paths. Graceful ACPI shutdown must work.

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
publication. QEMU bundling, macOS HVF packaging, Windows WHPX packaging and those
host validations remain separate work.
