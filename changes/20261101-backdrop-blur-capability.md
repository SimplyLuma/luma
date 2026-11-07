---
type: improvement
component: Shell
packages: [gnome-shell, gnome-shell-common, luma-shell-state]
---
Summary: Turn off backdrop blur without making surfaces opaque
Details: Devices where live blur is costly can switch blur off on its own, keeping translucent Shell surfaces; Reduce Transparency still makes them opaque.
