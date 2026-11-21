---
type: fix
component: Viola
packages: [viola-browser-stable]
highlight: false
---
Summary: Viola stays quiet when idle
Details: With two Viola windows open, the windows kept redrawing their sidebars dozens of times a second even when nothing changed, using several CPU cores. They now update only when something changes, and the window draws a sidebar update at most once per frame.
