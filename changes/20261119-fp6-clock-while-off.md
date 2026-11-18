---
type: fix
component: Phone
highlight: false
---
Summary: The Fairphone 6 keeps the time while switched off
Details: After the Fairphone 6 was switched off, its clock picked up where it stopped and stayed behind until it reached the network. It now reads the phone's own hardware clock at start, so it starts at the right time. Needs luma-fp6-platform 0.1.0-1.luma.22.
