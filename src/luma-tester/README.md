# Luma Tester

A Linux host launcher for Luma Mobile and Desktop QEMU developer images. Mobile
is available alongside Desktop. Both x86_64 and aarch64 guest definitions are supported;
matching host/guest architectures use KVM when accessible. Other combinations use
QEMU TCG and can be much slower. An Apple Silicon macOS packaging recipe is available; Windows host packaging remains future work.

The launcher requires the shared LumaUI runtime: its AppWindow, islands, cards,
type scale and icons. It displays the real guest inside that LumaUI frame through
GVnc and GTK4, over a private Unix socket. It does not use a stock GTK launcher,
a GTK3 viewer, browser rendering, or screenshot polling. Pointer, keyboard and
scroll input go to the real QEMU guest. Ctrl+Alt returns focus to the launcher.

Install distribution packages for Python 3.11+, PyGObject, GTK >= 4.18, libadwaita >= 1.7,
QEMU system emulation (including its GTK display backend), qemu-img, xorriso,
OpenSSH, rsync, GVnc >= 1.5 (the `gvnc` package, including introspection data), plus `luma-developer-platform` (including LumaUI and its Python
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
`luma-tester` command and desktop entry. QEMU, GVnc, GTK and LumaUI remain native
runtime dependencies. See [runtime notices](THIRD-PARTY.md).

## Guest images

The interface starts with **Try Luma** or **Develop for Luma**, then opens a
shared simulator workspace. Choose an experience and build in its sidebar;
**Start**, **Pause/Resume**, and **Shut Down** stay in the bottom action bar. Desktop is
initially selected when available and prefers the host CPU architecture.
Mobile prefers a qualified ARM64 image, with an explicitly identified x86_64
simulator available for the same native phone shell. Develop adds project
SSH access, a copyable local-agent prompt, shared folders and device controls. Named presets
retain an exact image, experience, workspace mode and VM settings. Named disk snapshots preserve guest changes, and closing the app gracefully shuts down the guest. Tablet, TV and Smart Device
require dedicated future profiles and remain unavailable. Imports and presets
are remembered locally; release catalogs remain an image qualification milestone.

Tester 0.3.0 supports anonymous [Hub download attribution](../../docs/developer-platform/tester-downloads.md).
Private previews use `source: test`. Catalog lookup and cached launches create no
tickets. Published file downloads retain a ticket across retries and report
completion only after size/SHA-256 verification. Installer ISOs in the public
catalog are separate from ready-to-run developer session disks.

There is no default public image feed yet. For this developer milestone, add a
local image manifest using **Add Image…** in the workspace sidebar. A packaged image catalog can be supplied through
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

`stop` requests guest shutdown through SSH and systemd (a paused VM is resumed
first). Mobile treats the virtual power button as screen sleep/wake, so an ACPI
button press is not used for the launcher’s Shut Down action; pause/resume
are QMP controls. The standalone CLI can still open QEMU's own GTK display; closing that window
terminates its VM. Shut down from the guest first to let apps save their work. The GUI stays open while its guest
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

The embedded display check requires ImageMagick and the native runtime:

```sh
PYTHONPATH=src/luma-tester xvfb-run -a python3 src/luma-tester/tests/display_smoke.py
```

It connects to a real private QEMU socket and compares the GTK4 framebuffer pixels
with QEMU's own screenshot, then checks coordinate mapping and input dispatch.

TV, Tablet and Smart Home prefer ARM64 and can reuse the same generic image.
Add a generic UEFI ISO to install into a persistent disk, or import a prepared
developer manifest for managed SSH and agent access. The requested experience
is passed separately through fw_cfg; OS interface support remains separate work.
