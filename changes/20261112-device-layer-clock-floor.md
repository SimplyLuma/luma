---
type: fix
component: Phones
packages: [luma-update]
highlight: false
---
Summary: Phones install their device packages on first start
Details: A new phone without network or a saved clock starts at the system image's build time, and a device kit signed later was refused, so the phone started without its device packages. The clock now moves forward to the kit's signature time before the packages are installed, and a failed install is retried on the next start instead of counting toward the retry limit.
