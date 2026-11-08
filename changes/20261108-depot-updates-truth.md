---
type: fix
component: Depot
packages: [luma-application-installer]
highlight: false
---
Summary: Depot shows waiting app updates
Details: Shortly after a restart, Depot's Updates page could say every app was up to date while updates were waiting. It now checks first, says so while it checks, lists what is waiting and shows when apps are installing in the background, with Update now to install the rest at once.
