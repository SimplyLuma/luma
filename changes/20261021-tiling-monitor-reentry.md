---
type: fix
component: Desktop
packages: [gnome-shell-extension-tiling-shell]
highlight: false
---
Summary: Fixed missing tiling zones after returning to a monitor
Details: Reset drag selection state when leaving a monitor so returning to the same position rebuilds its zones.
