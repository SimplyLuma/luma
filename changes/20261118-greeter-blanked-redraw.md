---
type: fix
component: Shell
packages: [gnome-shell, gnome-shell-common]
highlight: false
---
Summary: Login screen stops redrawing while blanked
Details: After the login screen blanked on its own, the Shell kept redrawing it every frame, which used battery while the screen was off. Keys typed on the login screen also reach the password field again after it wakes.
