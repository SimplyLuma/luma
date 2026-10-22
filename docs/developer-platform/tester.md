# Develop with Luma Tester

Luma Tester runs a complete guest OS in QEMU. One Linux launcher handles mobile
and desktop, with separate image manifests for each architecture and build.
Start with a trusted **Luma simulator/developer image**, not a Fairphone disk,
rootfs tarball, Android APK, or desktop installer ISO. There is no public guest
feed in this first implementation; image preparation is documented in
[tester-images.md](tester-images.md).

The current private preview includes Desktop and the genuine native Mobile
shell. Mobile touch, keyboard, Home and app hotfix workflows have been exercised
on an x86_64 simulator. Physical phone hardware and ARM binaries require their
own qualification.

## Portable Linux preview

The portable directory includes LumaUI, Python, QEMU, SSH and rsync. Guest images
can be downloaded, delivered separately, or embedded for offline use. Open
**Luma Tester** inside it. Mark the AppImage executable before opening. Without
FUSE, use `./Luma-Tester-0.3.0-linux-x86_64.AppImage --appimage-extract-and-run`.
Use the included README for package-specific commands. These are Linux x86_64
builds; ARM guest execution and macOS/Windows packages need separate qualification.

Device type and CPU architecture are independent. Desktop selection prefers the
host architecture: ARM64 on Apple Silicon or other ARM64 hosts, x86_64 on Intel
and AMD. Mobile prefers an available ARM64 phone image. A qualified x86_64
Mobile simulator can run the same phone shell and responsive apps for faster
development; it does not qualify ARM64 app binaries or physical phone hardware.
If only one qualified image is available, the launcher uses it. Cross-architecture
execution requires emulation and can be slower. The CLI accepts a particular
manifest when you need an explicit simulator or reference image.

For a portable Linux build, run `"/path/to/Luma Tester" install-desktop` to add
its persistent executable and icon to your user Applications menu and dock.
For an AppImage without FUSE, insert `--appimage-extract-and-run` before
`install-desktop`. Register it again after moving the executable.

The source packagers are `scripts/tester/build-portable.py` and
`scripts/tester/build-appimage.py`; both accept `--help`. They run inside the
normal admitted build lane, consume explicit runtime/catalog inputs, and write
only their output directories. No public download is configured by building them.
The default lightweight mode retains published HTTPS image URLs and downloads a
verified image on first use. `--sidecar-images` keeps private preview images in
an `images/` directory beside the AppImage; replacing the app does not replace
those assets or existing writable workspaces. `--offline` embeds the disks for
a full offline bundle. Automatic latest-build discovery and update prompts need
a published versioned image feed; this private preview does not configure one.
Anonymous [Hub download tickets](tester-downloads.md) distinguish published
downloads from cached launches. Private previews and QA use test attribution.

## Start a session

Install the [host requirements](../../src/luma-tester/README.md), then run:

```sh
src/luma-tester/luma-tester gui
```

Choose **Try Luma** or **Develop for Luma**. Both open the same workspace:
choose **Mobile** or **Desktop** in the sidebar, choose an image from the **Images** menu, then **Start**.
The sidebar uses compact LumaUI destination rows. Unavailable devices are marked
**Soon** and open an explicit no-build state. Presets appear as sidebar items;
the section’s **+** opens a native modal for saving the current configuration.
The floating bottom action bar provides **Pause**, **Resume**, and graceful **Shut Down**. Mobile
also provides **Home**, which opens the actual phone Home surface. Stop the
current session before changing its device experience or image. On narrow
windows the sidebar opens over the content using the toolbar's navigation button.
The launcher
chooses a native architecture image from its catalog when available. On first use
it downloads/verifies the disk and creates a private key and writable overlay;
subsequent runs keep that workspace. The guest appears inside the LumaUI window. Its live display uses a private Unix
socket; pointer, keyboard and scrolling operate the actual guest. Ctrl+Alt
returns focus to launcher controls.
The composed Desktop image identifies its virtual monitor as **Luma Desktop**
and defaults to **1920 × 1080 at 100% scale**. Its aspect-only display identity
does not claim a physical monitor size. Change resolution or scaling in the
guest's display settings; those preferences remain in that session's overlay.
Mobile requests a single 432×960 portrait output; the image's compositor must honor the
virtual output and use the handheld device class. The image determines scaling.

Use **Add Image** to import a trusted developer manifest, or set
`LUMA_TESTER_CATALOG` to a directory of qualified manifests. The image picker
separates the latest qualified included image, installed/local images and
images available to download. It does not substitute an installer ISO for a
ready-to-run developer disk. A public developer-disk feed remains separate from
Hub's current installer catalog.

Switch **Try / Developer** at the top of the sidebar at any time. The running
VM and its disk stay the same. Developer reveals **SSH access**, **Copy Agent
Prompt**, **Shared Folders** and **Devices**. Enable SSH, start Luma, then copy
the prompt into your local coding agent. Its separate private key can be revoked
by turning SSH access off; internal launcher control has a different key.
The enabled SSH section expands inline with the host, forwarded port, username
and a Copy SSH Command button. Connections use the managed SSH key; no password
is needed. Start the guest before copying its connection command.

Shared folders default to read only; **Allow changes** opts into edits on the
host. Selected folders appear at `/mnt/shared/1`, `/mnt/shared/2`, etc. Ownership
is translated inside the guest so host/guest UIDs need not match. A legacy guest
may install the small bindfs helper on first use; new phone images include it.
Audio output defaults on when a host PipeWire service is available. Microphone
and accessible USB cameras are opt-in. Hardware and folder changes apply on the
next start; mouse and keyboard input already forward through the display.

The settings icon at the top left opens memory, CPU, virtual disk and graphics
settings. Virtual disks grow sparsely and cannot be shrunk through this UI.
The guest filesystem may need expansion after increasing an existing disk.

Closing Tester requests a graceful guest shutdown and preserves its disk, key,
selected build and workspace mode. Reopening returns to that workspace. **Snapshots
→ +** saves a named disk checkpoint after shutting down; choose it to restore
while stopped. Snapshots save disk contents, not RAM or shared host folders.
Keep host projects in their own version control. Restoring a checkpoint replaces
guest-disk changes since that checkpoint and requires an explicit Restore action.

The real Mobile compositor uses its native CPU renderer on two-dimensional
VirtIO hardware to avoid corrupt software scanout tiles; VirGL-capable guests
retain the normal accelerated renderer. GPU-only visual effects may differ in
the CPU fallback. Physical phone timing and GPU behavior still need device tests.

From a terminal, use an alias or installed `luma-tester` command:

```sh
luma-tester doctor /path/to/mobile.json
luma-tester prepare /path/to/mobile.json
luma-tester start /path/to/mobile.json --memory 4096 --cpus 4
```

`start` stays in the foreground for the guest lifetime. In a second terminal:

```sh
luma-tester wait /path/to/mobile.json --graphical --timeout 300
luma-tester ssh /path/to/mobile.json uname -m
luma-tester status /path/to/mobile.json
luma-tester pause /path/to/mobile.json
luma-tester resume /path/to/mobile.json
luma-tester home /path/to/mobile.json
luma-tester screenshot /path/to/mobile.json /tmp/luma-screen.ppm
luma-tester stop /path/to/mobile.json
```

`--embedded` exposes a private Unix display socket for the LumaUI client.
The ordinary CLI `start` still opens QEMU's own GTK window for diagnostics.
`--headless` removes the window and keeps QMP screenshots and SSH available.
The embedded display uses a private Unix VNC socket. GPU graphics uses VirGL
through the host render device when initialization succeeds; unavailable GPUs
fall back to software rendering. `--graphics virgl` requires acceleration,
`--graphics software` selects the 2D GPU, and `--render-node` selects a GPU.
The selected mode and fallback reason are recorded in the workspace's `graphics.json`.
`--software` independently forces TCG CPU emulation for diagnostics.
For a custom firmware location use `start ... --firmware /absolute/path`.
`start ... --dry-run` prints the exact QEMU argv without downloading or starting
anything; it still needs firmware for UEFI targets.

## Iterate on an application

Use a project directory containing only files suitable for the guest. Sync is
explicit and copies into `~/Projects/app` inside the VM. It excludes `.git`,
`.env`, `.env.*`, `.venv`, and Python caches. Audit other secret files yourself.
Sync preserves permissions and safe symlinks, and never deletes guest files.
Host paths are not mounted into the guest.

```sh
luma-tester sync /path/to/mobile.json ./my-app
luma-tester ssh /path/to/mobile.json ls Projects/app
luma-tester dev /path/to/mobile.json ./my-app -- python3 -m my_app
```

`dev` waits for SSH and the painted graphical session, syncs, then starts `luma-tester-app.service` in the guest user
manager. It scans source metadata once per second and repeats sync/restart after
changes. Ctrl+C stops the watcher; the guest and app stay available for inspection.
This is a process restart, not in-process hot reload. Watcher sync does not remove
deleted files; rename/removal fixes may require explicit guest cleanup. Install
native/compiled dependencies inside the guest or cross-compile for its architecture.
An x86 host binary cannot execute in an ARM guest just because it was copied.
Use a single watcher per session; it owns that one guest app service name.

The guest graphical login must export `WAYLAND_DISPLAY`, `DISPLAY` if used, and
its D-Bus environment into `systemd --user`. This is part of the image contract;
do not guess a Wayland socket or run the app as root. Inspect app output with:

```sh
luma-tester ssh /path/to/mobile.json journalctl --user -u luma-tester-app -n 80 --no-pager
luma-tester ssh /path/to/mobile.json systemctl --user show-environment
```

For the included `examples/tester-hello`, run `dev ... -- python3 main.py` and
edit `message.txt`. Give mobile apps a declared minimum width that fits the
432-pixel viewport; an oversized minimum can prevent the compositor from
maximizing them. The guest includes the Luma SDK CLI (`luma --help`).

For an app with its own file watcher, use `sync` plus that app's existing reload
mechanism. Source synchronization does not rebuild RPMs, native code or Flatpaks.
For OS/shell changes, build and install the matching guest packages, then restart
the relevant **guest** service. Do not replace your host GTK or compositor.

## Instructions for a local coding agent

Give Claude, Codex, or another local tool this document and the paths to your app
and selected manifest. The same CLI works without GTK or a browser tool:

> Work in this app checkout. Use Luma Tester with the supplied manifest. Check
> `doctor`, start the guest if needed, wait for SSH, then sync and run the app using
> `dev`. Use guest journal output and QMP screenshots to verify changes. Keep host
> files, production profiles and guest data intact. Test at mobile and desktop
> widths using images built for those targets. Report the guest build/architecture,
> commands and observed results; distinguish virtual behavior from device testing.

Keep app changes in your normal host Git checkout. The VM is a test workspace,
not your source of truth. You can use a separate `--state` directory for experiments;
do not run two guests on the same forwarded SSH port.

## Accuracy and diagnostics

Matching architectures use KVM if `/dev/kvm` is accessible. Cross-architecture
sessions use TCG: useful for ABI/packaging checks, with no promise of interactive
speed or comparable performance. Native x86 mobile userspace can offer fast
layout iteration while ARM validates architecture-specific behavior. The image
must actually contain the same shared platform, mobile shell and apps intended
for Luma; a portrait window alone does not establish parity.

QEMU provides virtual virtio devices and a generic ARM `virt` or x86 `q35` board.
It does not emulate Fairphone/Pixel SoCs. Real hardware is still needed for modem,
camera, GNSS, fingerprint, power/thermal and device kernel/driver qualification.
Scenario state in the older FP6 twin is synthetic, and is not automatically wired
to all real apps. The tester adds no claims of sensor or radio simulation.

`Advanced → Open Workspace` contains `launcher.log`, `console.log`, `command.json`, the pinned
manifest, and connection metadata. Logs can contain guest data; review before
sharing. If SSH fails, inspect cloud-init and sshd in the guest. If QEMU cannot open
GTK, install its GTK display module or use headless mode. A port collision fails
startup; use another `--port` for a second guest. A cached image checksum failure
requires removing just that stopped image's cache file and preparing again.

QEMU reference: [system invocation](https://www.qemu.org/docs/master/system/invocation.html),
[ARM virt board](https://www.qemu.org/docs/master/system/arm/virt.html), and
[virtio GPU](https://www.qemu.org/docs/master/system/devices/virtio/virtio-gpu.html).


### Device experience and architecture

Desktop is initially selected when its image is available, including on ARM
hosts. Mobile remains an explicit experience choice.

The workspace keeps two independent choices: an experience profile and a guest
CPU architecture. The existing OS/AppKit contract uses `LUMA_DEVICE_CLASS`
(`desktop`, `tablet`, `handheld`), `LUMA_PRESENTATION_MODE` (`windowed` or
`fullscreen-mobile`), and `LUMA_INPUT_MODE` (`pointer` or `touch`). The native
phone simulator sets `handheld`, `fullscreen-mobile`, and `touch`; it starts
Phosh/Phoc from the phone source, including its actual Home and keyboard.
Application layout still responds to its available logical window size.

Tablet, TV, and Smart Device entries reserve navigation destinations and stay
unavailable until their dedicated images and runtime profiles are implemented.
TV/Smart Device and foldable/dual-screen topology are not yet added to the OS
capability contract. Changing a viewport must not silently change CPU architecture
or stand in for a real shell profile. Fold/hinge posture and multiple displays
need explicit topology capabilities rather than a single width threshold.

The image picker accepts the existing checksummed manifests. Named presets save
the experience, workspace mode, and exact image identity. Loading a preset
requires its manifest to remain available; a changed nightly requires choosing
the build again. Stop the running session before loading another preset. Raw ISO installation,
camera/USB passthrough, Mac/Windows host packaging, and ARM image
qualification remain separate implementation work. A visible sidebar destination
does not mean that device has been qualified. Keyboard and pointer interaction
already reach the guest through the embedded display.

The native x86 phone producer is `scripts/tester/build-phone-guest.py`. It consumes
an explicit checksummed component/keyboard bundle and a verified common OS base,
installs the native phone shell in a disposable VM, and exports a separate Mobile
disk. Its build receipt deliberately sets `qualified: false`; run the actual
phone boot, input, and app workflow qualification before cataloging that image.

### TV, Tablet and Smart Home development

These experiences prefer ARM64 even on an x86 host. Desktop prefers the host
architecture. Mobile still requires a genuine Mobile image; a Desktop image
is never substituted for it. TV, Tablet and Smart Home reuse a shared generic
Desktop ARM image while their OS interface modes are developed. An explicitly
selected x86 image is permitted for development; it is never selected silently
for these ARM experiences. ARM on x86 uses QEMU CPU emulation.

Use **Add Image** to select a developer JSON manifest or a local `.iso`. For an
ISO, select the architecture of the media (ARM64 is preselected for these
experiences). Use a generic UEFI installer compatible with QEMU `virt` for ARM
or `q35` for x86. Raspberry Pi disk images require a separate physical hardware
profile; generic VM boot does not qualify Pi firmware, GPU or peripherals.
The ISO gets its own persistent installation disk and UEFI variable store. In
Virtual Machine settings, turn **Boot from ISO** off after installation. The
ISO remains attached read-only; the saved disk becomes the first boot choice.
Installer/live media has no Tester-managed SSH account or readiness service.
The display is available during firmware and installer startup. Use a prepared
developer image for the managed SSH, agent prompt and automatic folder mounts.
If the guest ignores an ACPI shutdown, Force Stop becomes available with a
confirmation; this can lose unsaved guest changes. Disk snapshots cover the
guest disk, not UEFI variables or host shared folders.

The CLI supports the same workflow:

```sh
luma-tester import-iso ./luma-arm64.iso --arch aarch64
luma-tester start /path/printed/by/import-iso.json --experience tv
```

#### Proposed OS experience hint

The Tester passes a QEMU fw_cfg string named `opt/luma/experience` with one of
`desktop`, `mobile`, `tablet`, `tv` or `home` (Smart Home). A Linux guest can read
`/sys/firmware/qemu_fw_cfg/by_name/opt/luma/experience/raw` when fw_cfg sysfs is
available. This is a requested interface, independent of CPU architecture,
physical-device identity and image checksum. The Tester does not change
`/etc/luma-device-class`, patch the ISO or claim the OS already implements these
modes. The OS can later use the hint for initial setup, with an installed user
choice or Quick Settings override taking precedence. That OS policy and its
physical-device persistence are separate work. Presets retain the experience
while multiple experiences may reference the same installation disk.

Linux x86-64 portable builders can include ARM guest support using an installed
`qemu-system-aarch64` and `edk2-aarch64`, or pass `--arm-qemu`, `--arm-rpm`,
`--arm-firmware` and `--arm-firmware-rpm` to `build-portable.py` for privately
extracted matching packages. The producer checks extracted files against RPM
digests and keeps the QEMU loader, modules, dependencies and firmware together.
This adds ARM guests; macOS/Windows host packaging remains separately qualified.
