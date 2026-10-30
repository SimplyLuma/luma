# Titan web sandbox compatibility

Owner: Codex / luma-dev-2. Branch agent/1003/titan-web-sandbox-20261029.
Worktree /srv/dev/worktrees/luma-os/titan-web-sandbox-20261029.
Scope: opt-in procfs mount for boot profiles replacing /proc/cmdline; enables
normal Bubblewrap/WebKit sandbox startup without disabling it. Original Titan
worktree's ongoing application layout changes are preserved independently.
Native before/after Bubblewrap checks passed as described in the compatibility
note; persistent mount delivery and unit checks pending. No kernel rebuild.

Delivered owner device unit `/etc/systemd/system/run-luma-proc.mount`, enabled
and active. Bubblewrap smoke succeeded with only this persistent mount after
removing the temporary probe mount. Native WebKit web/network processes start;
no sandbox-disabling flags added. Server `dev-titan-proc-unit-check-63265135`
and phone `systemd-analyze verify` both exited0. Reboot not tested. Source
candidate ready for integration. Device captures remain in private experiments.
