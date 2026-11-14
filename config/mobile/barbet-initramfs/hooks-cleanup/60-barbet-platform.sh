#!/bin/sh
# Barbet platform: copy the device units and profile into the mounted rootfs.
# This runs from init_2nd.sh's hooks-cleanup, after /sysroot is mounted and
# before switch_root. /usr is immutable on the OS image, so everything lives
# under /etc and /var.
set -eu
[ -d /sysroot/etc ] && [ -d /luma-barbet/etc ] || exit 0

cp -a /luma-barbet/etc/. /sysroot/etc/

mkdir -p /sysroot/etc/systemd/system/multi-user.target.wants
for unit in luma-barbet-audio.service luma-barbet-wifi.service; do
    ln -sf "../$unit" "/sysroot/etc/systemd/system/multi-user.target.wants/$unit"
done

# The audio route owns the mixer state; ALSA restore must not overwrite it.
for unit in alsa-state.service alsa-restore.service; do
    ln -sf /dev/null "/sysroot/etc/systemd/system/$unit"
done
