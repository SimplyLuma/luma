---
type: improvement
component: Desktop
packages: [gnome-shell, gnome-shell-common]
---
Summary: Lighter blurred surfaces and a smoother search overview
Details: Blurred surfaces such as the dock reuse their drawing buffers and skip two copies each frame, and they stop redrawing their blur while a dimmed dialog such as search is open, so the overview opens more smoothly, most noticeably on a Raspberry Pi.
