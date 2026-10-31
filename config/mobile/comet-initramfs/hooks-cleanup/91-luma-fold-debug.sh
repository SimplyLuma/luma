#!/bin/sh
# Fold bring-up: with luma.fold_wldebug=1, log phosh Wayland traffic and keep
# journald from rate-limiting it; otherwise remove those settings again.
[ -d /sysroot/etc/systemd ] || exit 0
d=/sysroot/etc/systemd/user/mobi.phosh.Shell.service.d
j=/sysroot/etc/systemd/journald.conf.d
s=/sysroot/etc/systemd/system
if grep -q "luma.fold_wldebug=1" /proc/cmdline; then
	mkdir -p $d $j
	printf "[Service]\nEnvironment=WAYLAND_DEBUG=1\n" > $d/99-fold-debug.conf
	printf "[Journal]\nRateLimitBurst=0\nRateLimitIntervalSec=0\n" > $j/99-fold-debug.conf
else
	rm -f $d/99-fold-debug.conf $j/99-fold-debug.conf
fi
# Keep the journal small enough to pull over USB (debug logging grew it past
# 1.5 GB), and keep user sessions in the system journal so a hard reset does
# not lose them with a half-written user journal.
mkdir -p $j
printf "[Journal]\nSystemMaxUse=256M\nSplitMode=none\n" > $j/80-luma-fold-size.conf
# Files written here carry no SELinux label and journald may not read them:
# label them once the root is up, then let journald reread its config.
cat > $s/luma-fold-journald-conf.service <<"UNIT"
[Unit]
Description=Fold bring-up: label the journald drop-ins and apply them
DefaultDependencies=no
After=systemd-journald.service local-fs.target
Before=sysinit.target
ConditionDirectoryNotEmpty=/etc/systemd/journald.conf.d
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'restorecon -R /etc/systemd/journald.conf.d 2>/dev/null || chcon -R --reference=/etc/systemd/journald.conf /etc/systemd/journald.conf.d'
ExecStart=/usr/bin/systemctl restart systemd-journald.service
[Install]
WantedBy=sysinit.target
UNIT
mkdir -p $s/sysinit.target.wants
ln -sf ../luma-fold-journald-conf.service $s/sysinit.target.wants/luma-fold-journald-conf.service
