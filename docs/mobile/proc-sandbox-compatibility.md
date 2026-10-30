# Application sandboxes and a replaced kernel command line

Some development mobile initrds bind a synthesized command line over
`/proc/cmdline` for OSTree. On the owner Titan this made Bubblewrap's proc mount
fail with EPERM in an unprivileged user/PID namespace. A proc filesystem with a
covered file is not an unobstructed proc view for the kernel's namespace mount
visibility checks. User and PID namespaces themselves were enabled.

`config/mobile/systemd/run-luma-proc.mount` is an opt-in compatibility mount for
these boot profiles. It mounts a complete proc view at `/run/luma/proc`, with
nosuid/nodev/noexec, in the existing PID namespace. It retains the development
command-line view at `/proc/cmdline`, ordinary proc access controls and browser
sandboxing. It does not change sysctls, capabilities, SELinux or browser flags.
The mount must be writable like the normal proc mount: a read-only view did not
permit Bubblewrap's normal mount request in the observed kernel.

Install the unit in `/etc/systemd/system/`, then enable it with
`systemctl enable --now run-luma-proc.mount` on an affected development device.
It has a mountpoint condition and is not added to general handheld presets.
Do not deploy it to devices that do not replace `/proc/cmdline`.

Before and after, run as the ordinary graphical user:

```sh
bwrap --ro-bind / / --unshare-user --unshare-pid --proc /proc /usr/bin/true
```

On the owner Titan, this failed before and passed after adding the unobstructed
view. Native WebKit then started its sandboxed processes. Reboot persistence
must be checked separately; unit verification is not a reboot qualification.
Rollback: close affected applications, disable the unit, then stop the mount.
The preferred long-term boot design avoids replacing individual proc files.
