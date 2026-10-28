# Luma Tester runtime dependencies

The embedded display uses GVnc from [GTK-VNC](https://gitlab.gnome.org/GNOME/gtk-vnc),
version 1.5.0 or compatible, under LGPL-2.1-or-later. It handles the RFB protocol;
Luma Tester's GTK4 picture and input adapter are original Luma code. No GTK-VNC
source is copied into this directory. Preserve GVnc's upstream license and source
availability when bundling it. Fedora supplies the library and introspection data
in its `gvnc` package.

LumaUI, GTK, libadwaita, PyGObject, QEMU, OpenSSH, rsync and xorriso retain their
existing licenses. See the repository licensing policy and each distribution
package's notices. A launcher wheel does not include those native dependencies.

The Apple Silicon GPU runtime uses QEMU with VirGL and ANGLE's Metal backend.
It is isolated from the app's GTK libraries and the host's installed tools.
`Resources/mac-gpu/runtime.json` records exact input checksums and source pins;
`Resources/mac-gpu/ThirdPartyNotices` contains the component licenses. The
release's corresponding-source archive includes the QEMU fork and build recipe.
`stage-macos-gpu.py` relocates qualified inputs; `build-macos.py --gpu-runtime`
adds that prefix to the app. The stock x86 emulator remains available separately.
