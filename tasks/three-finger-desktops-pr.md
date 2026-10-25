# Three-finger desktop gestures

- Owner: luma-dev-2
- Branch: fix/three-finger-desktops
- Worktree: /srv/dev/worktrees/luma-os/three-finger-desktops-pr
- Status: validated source; PR preparation

Use the existing shared touchpad gesture path for three-finger desktop switching.
Preserve explicit preferences and leave optional app switching disabled by default.
Keep app switching disabled when the overview closes. No device-specific fork.

Validation: normal job06dd5c4b passed strict schema compilation, JavaScript
syntax, actual native GSettings defaults/preservation, and isolated execution
of the patched initialization and overview lifecycle. Shared session gesture
scenario updated but not run. Physical gesture confirmation and package build
remain pending. Package identities advanced for Shell and shell-state.
