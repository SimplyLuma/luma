# Waydroid overlay files

Installed into `/var/lib/waydroid/overlay/` (Waydroid mounts it over the
Android image when `mount_overlays = True`). Owner root, `0644`; scripts
`0755`. Android reads them at container start.

## `system/etc/luma-task-recovery.sh` + `system/etc/init/luma-task-recovery.rc`

**Symptom:** an Android app (Reddit) reopened on the desktop over and over, a
few minutes apart, after being closed. No launch request was ever sent: the
window was reappearing, not the app being started.

**Cause:** Android's system process crashed once (SurfaceFlinger went away
while it showed an error dialog; `Adding window failed … DEAD_OBJECT`). Zygote
restarted it, but the Waydroid task HAL kept its binder to the old
ActivityTaskManager (`resizeTask(n) failed with exception -129`) and the
hwcomposer kept its binder to the task HAL. Closing a window on the desktop
calls `removeTask` through that dead chain, so the task stayed visible and its
window came back whenever it drew.

**Fix:** a root service started at `sys.boot_completed` watches zygote's pid;
when it changes it restarts the task HAL, waits for it to register, then
restarts the hwcomposer so it fetches the new one, and settles for a minute
before watching again (no restart loops). Verified on the ThinkPad on
2026-09-12 by killing `system_server` with an app open: recovery logged within
5 s, no further task HAL failures.

The proper fix is in the HAL and hwcomposer themselves (re-acquire the service
on `DEAD_OBJECT`); these files cover the gap until that rebuild.
