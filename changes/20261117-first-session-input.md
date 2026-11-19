---
type: fix
component: Desktop
packages: [mutter, mutter-common]
---
Summary: Keyboard and mouse work in the first session after setup
Details: After first-boot setup signed in, the desktop could open without responding to the keyboard, mouse or touchpad until the computer restarted. The first session now picks up every input device as soon as it takes over the screen.
