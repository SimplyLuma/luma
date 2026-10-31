#!/bin/sh
# Fold bring-up: with luma.fold_outcycle=1, simulate fold/unfold without the
# hinge: stop the touch gate, then switch outputs between the panels a few
# times through wlr-randr (the new panel on first, then the old one off, as the
# gate does) while the journal captures the kernel's disable and enable
# paths. Only on debug images; removed otherwise.
s=/sysroot/etc/systemd/system
[ -d $s ] || exit 0
if grep -q "luma.fold_outcycle=1" /proc/cmdline; then
	cat > $s/luma-fold-outcycle.service <<"UNIT"
[Unit]
Description=Fold bring-up output switch test
After=multi-user.target
[Service]
Type=oneshot
ExecStart=/bin/sh -c '\
sleep 60; systemctl stop luma-fold-touch-gate.service; logger -t outcycle start; \
export XDG_RUNTIME_DIR=/run/user/1000; \
export WAYLAND_DISPLAY=$(ls /run/user/1000 | grep "^wayland-[0-9]*$" | head -1); \
for i in 1 2; do \
  logger -t outcycle "$i: fold (DSI-1 on, DSI-2 off)"; \
  timeout 20 /usr/bin/wlr-randr --output DSI-1 --on --pos 0,0 2>&1 | logger -t outcycle; sleep 1; \
  timeout 20 /usr/bin/wlr-randr --output DSI-2 --off 2>&1 | logger -t outcycle; \
  sleep 12; /usr/bin/wlr-randr 2>&1 | grep -E "^DSI|Enabled|failed" | logger -t outcycle; \
  logger -t outcycle "$i: unfold (DSI-2 on, DSI-1 off)"; \
  timeout 20 /usr/bin/wlr-randr --output DSI-2 --on --pos 4000,0 2>&1 | logger -t outcycle; sleep 1; \
  timeout 20 /usr/bin/wlr-randr --output DSI-1 --off 2>&1 | logger -t outcycle; \
  sleep 12; /usr/bin/wlr-randr 2>&1 | grep -E "^DSI|Enabled|failed" | logger -t outcycle; \
done; logger -t outcycle done'
[Install]
WantedBy=multi-user.target
UNIT
	mkdir -p $s/multi-user.target.wants
	ln -sf ../luma-fold-outcycle.service $s/multi-user.target.wants/luma-fold-outcycle.service
else
	rm -f $s/luma-fold-outcycle.service $s/multi-user.target.wants/luma-fold-outcycle.service
fi
