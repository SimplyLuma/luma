---
type: fix
component: Filer
packages: [nautilus, nautilus-extensions, luma-terminal]
highlight: false
---
Summary: Programs start from Filer
Details: Choosing Run, or double-clicking a program or AppImage after making it executable, failed silently. AppImages now open in Valet, which starts them, and other programs run in a new Terminal session in their folder. Filer's search can now use the system file index too.
