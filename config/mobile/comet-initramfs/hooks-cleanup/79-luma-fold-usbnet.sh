#!/bin/sh
# Fold bring-up: key-only root SSH, reachable over the generic image's USB
# NCM link (172.16.42.1) or Wi-Fi, so logs, boot flashing and reboots don't
# need fastboot. sshd is enabled for this boot only; the key and the drop-in
# persist in /var and /etc.
s=/luma-fold/usbnet
[ -d /sysroot/etc ] && [ -f $s/authorized_keys ] || exit 0
# Root SSH logins start a user manager too; keep session services that own
# the modem's messages (mmsd-tng) out of it.
mkdir -p /run/systemd/user/mmsd-tng.service.d
printf '[Unit]\nConditionUser=!@system\n' > /run/systemd/user/mmsd-tng.service.d/10-luma-fold-not-root.conf
[ -e /sysroot/usr/lib/systemd/system/sshd.service ] || exit 0
mkdir -p /run/systemd/system/multi-user.target.wants
ln -sf /usr/lib/systemd/system/sshd.service /run/systemd/system/multi-user.target.wants/sshd.service
mkdir -p /sysroot/etc/ssh/sshd_config.d
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' > /sysroot/etc/ssh/sshd_config.d/10-luma-fold.conf
h=/sysroot/var/roothome
[ -L /sysroot/root ] || h=/sysroot/root
mkdir -p $h/.ssh && chmod 700 $h/.ssh
install -m 0600 $s/authorized_keys $h/.ssh/authorized_keys
