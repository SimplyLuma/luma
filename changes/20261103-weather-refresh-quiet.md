---
type: fix
component: Home
packages: [luma-home-widgets]
---
Summary: Weather refresh no longer reports a failed service
Details: When Weather can't refresh, for example offline or at the login screen, Home keeps the last reading without logging a failed background service.
