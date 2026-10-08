#!/system/bin/sh
# Project Luma: when Android's system process restarts (zygote gets a new pid),
# the Waydroid task HAL is left holding a dead binder to ActivityTaskManager
# and the hwcomposer a dead one to the task HAL, so closing an app window on
# the desktop stops removing the app and its window keeps coming back.
# Restart the task HAL, wait for it to register, then the hwcomposer, which
# fetches the new one. A restart cascade settles before the baseline is
# taken again, so this never loops on its own restarts.
pid() { getprop "init.svc_debug_pid.$1"; }
baseline=$(pid zygote)
while true; do
    sleep 5
    now=$(pid zygote)
    [ -z "$now" ] || [ "$now" = "$baseline" ] && continue
    log -t luma-task-recovery "zygote restarted ($baseline -> $now); refreshing task HAL and hwcomposer"
    sleep 5
    old=$(pid task-hal-1-0)
    [ -n "$old" ] && kill "$old"
    i=0
    while [ "$(pid task-hal-1-0)" = "$old" ] && [ $i -lt 30 ]; do sleep 1; i=$((i + 1)); done
    sleep 2
    hwc=$(pid vendor.hwcomposer-2-1)
    [ -n "$hwc" ] && kill "$hwc"
    sleep 60
    baseline=$(pid zygote)
    log -t luma-task-recovery "settled; watching zygote $baseline"
done
