---
type: improvement
component: Desktop
packages: [gnome-shell, gnome-shell-common, mutter, mutter-common]
---
Summary: Faster on-screen keyboard and first frames
Details: Keycap draws key layers from cached textures and switches layers without relayout. The compositor's frame threads keep a minimum CPU utilization, so the first frame after idle no longer waits for the CPU to ramp up.
