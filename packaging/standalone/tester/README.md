# Luma Tester — Linux developer preview

Open **Luma Tester**, choose **Try Luma** or **Develop for Luma**, then select a
device and **Build**. Start, Pause, Home and Shut Down live in the floating bottom
action bar. Devices without a build are marked Soon.

The app includes LumaUI, QEMU and developer tools. Lightweight packages download
checksum-verified guest disks from their configured HTTPS image URLs on first
use. Private previews may supply a separate images directory beside the AppImage;
offline packages embed their disks. Desktop and native Mobile use separate
qualified image profiles. Replacing Tester preserves cached images and writable
workspaces. Automatic latest-build prompts require a published image feed.

For development, connect an app folder in Project and copy its development
command. The watcher syncs changes and restarts the app. Try examples/Hello Luma
with python3 main.py; edit message.txt to see the change in the guest.
DEVELOPING.md includes the CLI and local agent workflow.

This preview targets Linux x86_64. Native Mobile runs the real phone shell and
keyboard; it does not reproduce Fairphone-specific hardware or qualify ARM
binaries. GPU acceleration is selected when available. Read the exact build and
qualification receipts before redistribution; this is not a published OS release.
