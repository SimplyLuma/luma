# Luma Tester

A Linux host launcher for Luma Mobile and Desktop QEMU developer images. Mobile
is the default experience. Both x86_64 and aarch64 guest definitions are supported;
matching host/guest architectures use KVM when accessible. Other combinations use
QEMU TCG and can be much slower. macOS and Windows host packaging are future work.

The launcher requires the shared LumaUI runtime: its AppWindow, islands, cards,
type scale and icons. It does not offer a stock GTK fallback. The guest renders
in QEMU's own GTK window, preserving real OS/app rendering and input.
This initial version does not embed the guest display in the launcher.

Install distribution packages for Python 3.11+, PyGObject, GTK >= 4.18, libadwaita >= 1.7,
QEMU system emulation (including its GTK display backend), qemu-img, xorriso,
OpenSSH and rsync, plus `luma-developer-platform` (including LumaUI and its Python
`luma_appkit` bindings). UEFI images also require edk2 firmware. Fedora splits ARM
system emulation into a separate package. No root daemon or libvirt is required.

Run directly from this checkout:

```sh
src/luma-tester/luma-tester gui
src/luma-tester/luma-tester doctor /path/to/mobile.json
src/luma-tester/luma-tester start /path/to/mobile.json
```

Or install with `python3 -m pip install ./src/luma-tester` in your chosen Python
environment with access to distribution PyGObject. The package installs the
`luma-tester` command and desktop entry. QEMU and GTK remain system dependencies.

## Guest images

The interface starts with **Try Luma** or **Develop for Luma**, then **Mobile**
or **Desktop**. Mobile is selected first. The launcher chooses the matching
native guest architecture automatically. Develop reveals project connection,
source sync and a command to start the app watcher. The Try path contains none
of those development controls. Advanced image imports are remembered locally;
release catalogs remain an image qualification/distribution milestone.

There is no default public image feed yet. For this developer milestone, add a
local image manifest from **Advanced** in
the Luma Tester menu. A packaged image catalog can be supplied through
`LUMA_TESTER_CATALOG=/path/to/manifests`. A manifest can refer to a local
standalone qcow2 or an HTTPS image;
the launcher downloads on first use and verifies both byte count and SHA-256.
A checksum verifies the selected manifest's content, not its publisher: obtain
manifests from a trusted source. Guest images are executable operating systems.

A manifest identifies one target/architecture/build. Preparing a mobile image
is a separate job from producing a device installer. The existing FP6 twin is
an experimental Fedora/Phosh behavioral environment, not a finished Luma Mobile
image. See [image producer contract](../../docs/developer-platform/tester-images.md).

Image bases are cached by digest. Each manifest id gets a persistent qcow2
overlay, generated SSH key, cloud-init seed, known-hosts file and logs. The
launcher never writes to the supplied disk. Each new image/build needs a new
manifest id; existing workspaces are not silently rebased. Downloads and prepare
operations hold the session lock. A second launcher cannot reuse a running disk.

All state lives in `$XDG_DATA_HOME/luma-tester` (normally
`~/.local/share/luma-tester`), override with `LUMA_TESTER_HOME` or `--state`.
Do not share that folder: it contains guest data and developer SSH private keys.
Use `--state` before the subcommand. Different simultaneous guests need different
`start --port` values. SSH is forwarded on 127.0.0.1 only. Outbound guest networking
is enabled. No host filesystem, microphone, camera or USB device is shared by default.

`stop` requests an ACPI shutdown. Guest shutdown support is required; pause/resume
are QMP controls. Closing QEMU's guest window terminates the VM, so shut down from
the guest first to let apps save their work. The GUI stays open while its guest
or preparation process is active. No automatic overlay deletion is provided.

For screenshots, headless sessions, source sync, hot fixes and local agents, see
[developing with Luma Tester](../../docs/developer-platform/tester.md).

## Validation

```sh
PYTHONPATH=src/luma-tester python3 -m unittest discover -s src/luma-tester/tests -v
```

The suite covers image verification, persistent state, isolation, quoting,
architecture selection and real QEMU/QMP control where installed. It does not
qualify a Luma guest image, ARM hardware, or physical device behavior.

With the real LumaUI runtime installed, check the native interface under Xvfb:

```sh
PYTHONPATH=src/luma-tester:src/luma-platform/appkit xvfb-run -a python3 src/luma-tester/tests/ui_smoke.py
```

The flow test uses in-memory desktop settings. If ImageMagick and xdotool are
available it also captures welcome, target selection and narrow/dark screenshots
in `DEV_ARTIFACT_DIR` (otherwise a temporary directory).
