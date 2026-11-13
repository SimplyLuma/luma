---
type: fix
component: Phone
packages: []
---
Summary: Phone system services can write to the log
Details: Some system services on phones could not write to the system log, and each startup logged hundreds of security denials. These services now log normally.
