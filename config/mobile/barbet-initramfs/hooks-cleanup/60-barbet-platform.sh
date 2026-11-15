#!/bin/sh
# Barbet platform: copy the device units and profile into the mounted rootfs.
# This runs from init_2nd.sh's hooks-cleanup, after /sysroot is mounted and
# before switch_root. /usr is immutable on the OS image, so everything lives
# under /etc and /var.
set -eu
[ -d /sysroot/etc ] && [ -d /luma-barbet/etc ] || exit 0

cp -a /luma-barbet/etc/. /sysroot/etc/

mkdir -p /sysroot/etc/systemd/system/multi-user.target.wants
for unit in luma-barbet-audio.service luma-barbet-audio-rescan.service \
            luma-barbet-wifi.service luma-barbet-boot-success.service; do
    ln -sf "../$unit" "/sysroot/etc/systemd/system/multi-user.target.wants/$unit"
done

# The sound card is registered only after the ADSP boots, and WirePlumber
# misses it on a cold boot until a udev change event for the sound subsystem.
# Start the session audio services at login and let the rescan service send
# that event once WirePlumber is running.
mkdir -p /sysroot/etc/systemd/user/default.target.wants
ln -sfn ../luma-barbet-audio-session.service \
    /sysroot/etc/systemd/user/default.target.wants/luma-barbet-audio-session.service

# The audio route owns the mixer state; ALSA restore must not overwrite it.
for unit in alsa-state.service alsa-restore.service; do
    ln -sf /dev/null "/sysroot/etc/systemd/system/$unit"
done
