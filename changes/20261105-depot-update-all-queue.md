---
type: fix
component: Depot
packages: [luma-application-installer]
---
Summary: Update all queues every update
Details: Depot runs app updates one after another and shows the ones still waiting as Queued instead of failed. A failed update no longer stops the rest, and a queued update can be cancelled.
