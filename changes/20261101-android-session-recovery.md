---
type: fix
component: Applications
packages: [luma-android-runtime]
---
Summary: Reconnect Android apps during runtime recovery
Details: Android recovery now stops the old desktop session before starting the replacement runtime. Repeated recovery attempts are limited across runtime restarts.
