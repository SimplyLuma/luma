---
type: fix
component: Updates
packages: [luma-update, luma-os-remote]
---
Summary: Updates no longer roll back on their first start
Details: The check that confirms an update started correctly could fail on every update, so the computer went back to the previous version. It now passes when the update is healthy.
