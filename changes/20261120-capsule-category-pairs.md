---
type: security
component: Valet
packages: [luma-application-installer]
highlight: false
---
Summary: Every installed app is kept apart from the others
Details: Apps installed from .deb and .rpm files each get their own separation label, checked against every other installed app. Two apps could previously end up with the same one and read each other's private files.
