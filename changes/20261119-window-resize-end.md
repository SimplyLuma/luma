---
type: fix
component: Desktop
packages: [mutter, mutter-common]
highlight: false
---
Summary: Windows settle at their final size after resizing
Details: When you let go after resizing a window, the app is now told the resize is over, so it settles at the exact size you left it at.
