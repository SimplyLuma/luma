---
type: fix
component: Depot
packages: [luma-application-installer]
---
Summary: Depot records its window opening again
Details: The redesigned Depot opened normally but never recorded that its window was on screen, so Luma Vitals could not tell a working start from a stalled one. Every start is recorded again.
