# Luma Tester — Linux developer preview

Open **Luma Tester**. Choose **Try Luma** or **Develop for Luma**, then **Mobile**
or **Desktop**. The preview includes its LumaUI runtime, QEMU, developer tools
and one verified guest disk containing both profiles. No host package installation
or root daemon is required. First launch creates a private writable workspace.

For development, connect an app folder and copy the development command from
Project. Replace `YOUR_APP_COMMAND` with your app's command. The watcher syncs
source and restarts the guest app after edits. Try the included `examples/Hello
Luma` with `python3 main.py`; edit `message.txt` to see a change in the guest.
Keep app minimum widths suitable for the 432-pixel mobile viewport.

`DEVELOPING.md` describes the CLI and agent workflow. In this portable package,
use `"/absolute/path/to/Luma Tester"` wherever it says `luma-tester`. **Shut Down**
saves the workspace. The host app and guest have separate lifetimes.

This package targets Linux x86_64. KVM gives the fastest preview; QEMU can fall
back to software emulation. The guest uses actual Luma shell/apps and software
graphics. It does not reproduce Fairphone radios, cameras, sensors, GPU timing,
battery behavior or Android integration. ARM guests and other host operating
systems require separately qualified packages. Read the build receipt and
runtime notices before redistribution; this local developer build is not a
published OS release.
