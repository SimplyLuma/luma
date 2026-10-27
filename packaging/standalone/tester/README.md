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
