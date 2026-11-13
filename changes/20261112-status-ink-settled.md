---
type: fix
component: Shell
packages: [gnome-shell, gnome-shell-common]
highlight: false
---
Summary: Scrolling on phones no longer stutters
Details: The status bar picks its colour once the screen stops moving (or every two seconds while it keeps moving) instead of every few hundred milliseconds, which dropped frames during scrolls and animations.
