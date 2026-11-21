---
type: fix
component: Valet
packages: [luma-application-installer]
---
Summary: Apps that check for a running copy now open
Details: Some apps installed from a file closed straight away because they could not register their own name with the session. They now start normally.
