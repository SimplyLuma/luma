---
type: improvement
component: Desktop
packages: [gnome-shell, gnome-shell-common]
---
Summary: Lighter blurred surfaces
Details: Blurred surfaces such as the dock reuse their drawing buffers and skip two copies each frame, so the Shell uses less processor and graphics time while they are on screen, most noticeably on a Raspberry Pi.
