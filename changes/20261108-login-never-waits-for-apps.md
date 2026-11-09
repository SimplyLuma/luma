---
type: fix
component: First boot
packages: [luma-application-installer]
highlight: false
---
Summary: The login screen no longer waits for the bundled apps
Details: On a slow first boot the bundled apps finish installing after sign-in instead of holding back the login screen, appear as each one is ready, and a notification says when they are all set up. An interrupted setup carries on at the next start.
