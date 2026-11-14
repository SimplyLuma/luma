---
type: fix
component: Updates
packages: [luma-update]
highlight: false
---
Summary: A new phone kernel must start before it is kept
Details: After a kernel update, the phone keeps the new kernel only once it has started correctly. Before, the Fairphone 6 could keep a kernel that did not match the update, leaving no automatic way back.
