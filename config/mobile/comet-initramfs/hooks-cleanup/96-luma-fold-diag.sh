#!/bin/sh
# Fold bring-up: with luma.fold_debug=1, log touch IRQ counts and input
# mappings to the journal every 15 s; otherwise remove the service.
s=/sysroot/etc/systemd/system
[ -d $s ] || exit 0
if grep -q "luma.fold_debug=1" /proc/cmdline; then
	cat > $s/luma-fold-diag.service <<"UNIT"
[Unit]
Description=Fold bring-up input diagnostics
[Service]
ExecStart=/bin/sh -c "while :; do grep -i -E \"goodix|syna|berlin|tcm|gpn\" /proc/interrupts; for e in /sys/class/input/event*; do echo \"$${e##*/} $$(cat $$e/device/name) $$(udevadm info -q property -n /dev/input/$${e##*/} | grep -E \"WL_OUTPUT|ID_INPUT_TOUCHSCREEN\" | tr \"\\n\" \" \")\"; done; sleep 15; done"
[Install]
WantedBy=multi-user.target
UNIT
	mkdir -p $s/multi-user.target.wants
	ln -sf ../luma-fold-diag.service $s/multi-user.target.wants/luma-fold-diag.service
else
	rm -f $s/luma-fold-diag.service $s/multi-user.target.wants/luma-fold-diag.service
fi
