---
type: improvement
component: Ari
packages: [luma-ari-runtime]
---
Summary: Smaller Ari model runtime
Details: Ari talks to its local model server directly, so the server's own web interface is no longer built in. The runtime is smaller and its build no longer downloads anything at build time.
