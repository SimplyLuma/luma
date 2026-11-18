---
type: improvement
component: Shell
packages: [gnome-shell, gnome-shell-common]
---
Summary: Smoother status icons on phones
Details: On phones and tablets the status bar picks its icon color from what is behind it. It now reads the screen once instead of six times and does it outside the frame being drawn, so animations stay smooth while the color settles.
