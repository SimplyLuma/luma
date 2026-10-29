# Try Luma portable builds

Open **Try Luma**, choose Try or Developer, then Desktop, Tablet or Mobile.
The bottom action bar selects architecture and installation media and controls
the VM. Developer reveals SSH access, an agent prompt and sharing controls.

The lightweight app downloads approved, checksum-verified images when needed.
One ISO per architecture serves the experiences. Existing imported images,
workspaces and snapshots remain available when the application is updated.

The application ID `io.luma.Tester`, data directories and CLI command remain
stable through the display-name change. Do not rename or delete existing user
state. Generated packages use the **Try Luma** application name.

See `src/luma-tester/icon-concepts/README.md` for the icon directions, native
formats and Windows resource-compiler build dependencies. The final selected
icon must be embedded before package signing; changing it invalidates signatures.

DEVELOPING.md contains the CLI and local agent workflow. VM CPU emulation does
not reproduce the physical device's camera, modem or GPU drivers.

## Linux graphics qualification

The portable build includes the GTK renderer's dynamically loaded Mesa/GLVND
and Vulkan implementations as well as QEMU's graphics libraries. It pins driver
discovery to the bundle so a host distro's graphics libraries are not loaded
against its private libc. The builder checks ELF dependencies and symbol versions
before producing an AppImage, preserving the UI's own GTK, GLib and libc.

Run the graphics check with the portable Python inside the target X11 or
Wayland session, without a `GSK_RENDERER=cairo` override:

```sh
DEV_ARTIFACT_DIR=/path/to/results /path/to/portable/python scripts/tester/qualify-linux-graphics.py
```

The report records the actual GTK/OpenGL renderer and fails if a graphics
implementation was loaded outside the bundle. Run it in clean distro userspaces
and with inherited host driver paths to catch compatibility regressions. For
the separate no-GL/Vulkan fallback check, set `GDK_DISABLE=gl,vulkan` and pass
`--allow-cairo`. Xvfb validates software GL and library isolation; it does not
qualify a physical GPU, guest graphics performance or every host driver.
