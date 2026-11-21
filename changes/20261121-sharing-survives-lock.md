---
type: fix
component: Desktop
packages: [mutter, mutter-common, gnome-shell, gnome-shell-common, luma-portal]
highlight: false
---
Summary: Screen sharing survives locking the screen
Details: Locking no longer ends your shares. They pause while the screen is locked, so nothing is shared while you are away, and carry on after you unlock. Share requests made while the screen is locked wait and appear after you unlock, and the pointer always shows on the lock screen.
