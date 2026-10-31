# Drawer app actions

Run `run-native.sh` in the owned disposable native Shell fixture described in
`../applications-drawer/README.md`. The installed candidate must include the
current desktop drawer source. Private desktop entries and favorites never
reach the developer desktop.

Checks use actual pointer and keyboard events to open the drawer app menu,
pin and unpin through its visible actions, hold to open the menu, dismiss with
Escape, rebuild search results and close the drawer without leaving a menu or
modal grab behind. Screenshots and results remain private artifacts.
