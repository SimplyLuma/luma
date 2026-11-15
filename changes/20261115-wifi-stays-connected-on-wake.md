---
type: fix
component: Networking
packages: [NetworkManager]
---
Summary: Wi-Fi stays connected when the device wakes up
Details: After a suspend, NetworkManager no longer disconnects and reconnects a Wi-Fi network that stayed connected while the device slept. On a phone this removes about 13 seconds without network on every wake and keeps open connections alive.
