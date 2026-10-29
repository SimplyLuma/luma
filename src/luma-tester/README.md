# Try Luma

A LumaUI launcher for persistent Luma QEMU workspaces. Desktop, Tablet and Mobile
share the public OS ISO for each architecture: x86_64 or aarch64. Matching
host/guest architectures use KVM on Linux, HVF on macOS, or WHPX on Windows when
available. Other combinations use QEMU TCG and can be much slower.

Try Luma always uses dark LumaUI styling on Linux, macOS and Windows. It has no
light-mode option and does not change the host desktop appearance. Native host
packages require their own build and validation; see the task release evidence.

The launcher requires the shared LumaUI runtime: its AppWindow, islands, cards,
type scale and icons. It displays the real guest inside that LumaUI frame through
GVnc and GTK4, over a private Unix socket on Linux/macOS or password-protected
loopback TCP on Windows. It does not use a stock GTK launcher,
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
shared workspace. The sidebar mode switch can reveal developer controls without
restarting the guest. Choose Desktop, Tablet or Mobile in the sidebar and an
architecture and image in the bottom action bar. Desktop and Tablet prefer the
host architecture; Mobile initially prefers ARM64. An explicit architecture
selection overrides that preference. TV and Smart Device are coming soon.

The Images dropdown lists public Hub downloads alongside local workspaces.
Downloads show transfer and verification progress and are accepted only after
size and SHA-256 verification. One public OS ISO per architecture can be used
with Desktop, Tablet or Mobile; the OS's own presentation switcher also changes
its interface. Start automatically finds and downloads the approved image for
the selected architecture. On its first start, Try Luma uses the ISO to set up
a private persistent VM, then signs in to the desktop automatically. Setup can
take several minutes; subsequent starts boot the saved workspace directly.
The installer wizard is not shown. Only approved ISOs use automatic setup;
other imported ISOs retain their normal boot behavior. Setup uses a separate
staging disk and never reformats an existing workspace.
Preinstalled developer disks are separate imports and
may have narrower experience or developer-access capabilities.

**Start**, **Pause/Resume**, and **Shut Down** stay in the bottom action bar.
Developer controls include local SSH, a copyable agent prompt, shared folders
and device controls where supported by the host and guest. Named presets retain
an exact image, experience, workspace mode and VM settings. Named disk snapshots
can be saved while the guest runs. Persistent workspaces preserve guest disk
changes across shutdown and restart.

Tester supports anonymous [Hub download attribution](../../docs/developer-platform/tester-downloads.md).
Private previews use `source: test`. Catalog lookup and cached launches create no
tickets. Published file downloads retain a ticket across retries and report
completion only after size/SHA-256 verification.

Local ISO or manifest imports are also supported. A manifest can refer to a local
standalone qcow2 or an HTTPS image. A checksum verifies the selected manifest's
content, not its publisher: obtain manifests from a trusted source. Guest images
are executable operating systems. See the [image producer contract](../../docs/developer-platform/tester-images.md).

Image bases are cached by digest. Each manifest id gets a persistent qcow2
overlay, generated SSH key, cloud-init seed, known-hosts file and logs. The
launcher never writes to the supplied disk. Each new image/build needs a new
manifest id; existing workspaces are not silently rebased. Downloads and prepare
operations hold the session lock. A second launcher cannot reuse a running disk.

To remove a downloaded image, open **Images** in the bottom action bar and use
the trash button beside that image. The confirmation lists dependent workspaces
and presets: deleting a prepared image also deletes those workspaces and their
saved files and snapshots. Original imported files and shared host folders are
kept. Removing an installer ISO preserves completed, independent installations.
Images being downloaded, prepared or used by a running VM cannot be deleted.
Published images remain available to download again.

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
[developing with Try Luma](../../docs/developer-platform/tester.md).

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
