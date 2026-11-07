---
type: fix
component: Updates
packages: [luma-update, luma-os-remote]
---
Summary: Failed updates roll back on phones and computers
Details: An update installed some time before the restart could not return to the previous version if it failed to start. It now does, and the previous version stays in place afterwards instead of the failed update being tried again.
