#!/bin/sh
# Fold bring-up: test units that earlier debug images left in the root's /etc
# (they persist across RAM boots) are removed unless this boot asks for them,
# so a plain boot never runs a radio exerciser or waits on its reboot hold.
s=/sysroot/etc/systemd/system
[ -d $s ] || exit 0
grep -q "luma.fold_radiotest=1" /proc/cmdline && exit 0
rm -f $s/luma-fold-radiotest.service $s/luma-fold-radiotest.sh $s/multi-user.target.wants/luma-fold-radiotest.service \
	$s/luma-fold-autoreboot.service.d/90-luma-fold-radiotest.conf $s/luma-fold-radio-diag.service.d/90-luma-fold-radiotest.conf
rmdir $s/luma-fold-autoreboot.service.d $s/luma-fold-radio-diag.service.d 2>/dev/null
exit 0
