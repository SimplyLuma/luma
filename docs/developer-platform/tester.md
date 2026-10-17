# Develop with Luma Tester

Luma Tester runs a complete guest OS in QEMU. One Linux launcher handles mobile
and desktop, with separate image manifests for each architecture and build.
Start with a trusted **Luma simulator/developer image**, not a Fairphone disk,
rootfs tarball, Android APK, or desktop installer ISO. There is no public guest
feed in this first implementation; image preparation is documented in
[tester-images.md](tester-images.md).

## Portable Linux preview

The portable directory includes LumaUI, Python, QEMU, SSH, rsync and its verified
shared guest image. Open **Luma Tester** inside it. The AppImage packages the same
directory as one executable; mark it executable before opening. Without FUSE,
use `./Luma-Tester-0.1.0-linux-x86_64.AppImage --appimage-extract-and-run`.
Use the included README for package-specific commands. These are Linux x86_64
builds; ARM guest execution and macOS/Windows packages need separate qualification.

The source packagers are `scripts/tester/build-portable.py` and
`scripts/tester/build-appimage.py`; both accept `--help`. They run inside the
normal admitted build lane, consume explicit runtime/catalog inputs, and write
only their output directories. No public download is configured by building them.

## Start a session

Install the [host requirements](../../src/luma-tester/README.md), then run:

```sh
src/luma-tester/luma-tester gui
```

Choose **Try Luma** or **Develop for Luma**, then **Mobile** or **Desktop**.
Mobile starts selected. Click **Open Luma Mobile** (or Desktop). The launcher
chooses a native architecture image from its catalog when available. On first use
it downloads/verifies the disk and creates a private key and writable overlay;
subsequent runs keep that workspace. The guest appears inside the LumaUI window. Its live display uses a private Unix
socket; pointer, keyboard and scrolling operate the actual guest. Ctrl+Alt
returns focus to launcher controls.
Mobile requests a single 432×960 portrait output; the image's compositor must honor the
virtual output and use the handheld device class. The image determines scaling.

This developer milestone has no public image catalog. Use **Advanced → Add Image**
in the Luma Tester menu to add a trusted manifest, or set `LUMA_TESTER_CATALOG`
to a directory of qualified manifests. Imports are remembered. Architecture,
firmware and other QEMU overrides remain in the CLI. The launcher requires real
LumaUI; a stock GTK interface is not a substitute when its runtime is missing.

On the Develop path, **Connect a project** chooses your local app directory.
Once Luma is open, **Sync Project** copies it to the guest and **Copy Development
Command** gives you the watcher command for your terminal or coding agent.
Replace its explicit `YOUR_APP_COMMAND` argument with your app's launch command.

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
luma-tester screenshot /path/to/mobile.json /tmp/luma-screen.ppm
luma-tester stop /path/to/mobile.json
```

`--embedded` exposes a private Unix display socket for the LumaUI client.
The ordinary CLI `start` still opens QEMU's own GTK window for diagnostics.
`--headless` removes the window and keeps QMP screenshots and SSH available.
No unauthenticated VNC server is started. `--software` forces TCG for diagnostics.
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
