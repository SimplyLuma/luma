---
type: improvement
component: Desktop
packages: [gnome-shell, gnome-shell-common]
---
Summary: Faster focus changes and panels
Details: Panels and lists reuse the backgrounds and shadows they already drew, so moving the focus and opening the quick panel take less time, most noticeably on a Raspberry Pi.
