---
type: fix
component: Desktop
packages: [mutter, mutter-common, gnome-shell, gnome-shell-common, luma-portal]
highlight: false
---
Summary: Screen sharing shows the right screens and cleans up after itself
Details: Each screen in the share picker now previews just that screen, with its real name and resolution. The sharing pill no longer appears in what you share or flickers, and Dismiss hides it while a small sharing indicator stays in the status area. Rings around shared windows now disappear as soon as the share ends, even if the app quits or crashes.
