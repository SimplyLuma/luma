---
type: fix
component: Updates
packages: [luma-update]
highlight: false
---
Summary: Clear message when a channel has no updates yet
Details: A device whose update channel has nothing published yet was told Luma couldn't reach its update server. It now says no updates are published for this device's channel yet, and no longer checks again on every network change.
