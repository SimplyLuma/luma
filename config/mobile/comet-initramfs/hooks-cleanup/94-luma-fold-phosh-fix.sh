#!/bin/sh
# Fold bring-up: preload a shim that registers the layer-surface wl-output
# property with G_PARAM_CONSTRUCT (patches/phosh/0001), so the lock screen
# background stays on its own output with two panels.
[ -d /sysroot/etc/systemd ] && [ -f /luma-fold/luma-fold-phosh-fix.so ] || exit 0
mkdir -p /sysroot/var/lib/luma-fold /sysroot/etc/systemd/user/mobi.phosh.Shell.service.d
cp /luma-fold/luma-fold-phosh-fix.so /luma-fold/luma-fold-phoc-touch.so /sysroot/var/lib/luma-fold/
rm -f /sysroot/var/lib/luma-fold/phosh
# SELinux: the greeter (xdm_t) and apps must map and execute these libraries
s=/sysroot/etc/systemd/system
printf "[Unit]\nDescription=Fold bring-up: label /var/lib/luma-fold as libraries\nDefaultDependencies=no\nAfter=local-fs.target\nBefore=greetd.service display-manager.service\n[Service]\nType=oneshot\nExecStart=/usr/bin/chcon -R -t lib_t /var/lib/luma-fold\n[Install]\nWantedBy=multi-user.target\n" > $s/luma-fold-labels.service
mkdir -p $s/multi-user.target.wants
ln -sf ../luma-fold-labels.service $s/multi-user.target.wants/luma-fold-labels.service
# luma-phosh with the two-panel patches (0054-0056) needs no shim
d=/sysroot/etc/systemd/user/mobi.phosh.Shell.service.d
if grep -q "Unknown panel orientation" /sysroot/opt/luma/phosh/lib64/libphosh-0.45.so.0 2>/dev/null; then
	rm -f $d/98-luma-fold-fix.conf
else
	printf "[Service]\nEnvironment=LD_PRELOAD=/var/lib/luma-fold/luma-fold-phosh-fix.so\n" > $d/98-luma-fold-fix.conf
fi
