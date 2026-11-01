#!/bin/sh
# Screen off/on through the compositor, then report DECON frame timeouts and a screenshot.
D=$(cd "$(dirname "$0")" && pwd)
"$D/tokay-ssh.sh" 'W=$(ls /run/user/1000 | grep "^wayland-[0-9]*$" | head -1); U="runuser -u luma -- env XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=$W DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"
for n in 1 2 3; do
	$U wlr-randr --output DSI-1 --off; sleep 4
	a=$(dmesg | grep -c "frame timed out")
	$U wlr-randr --output DSI-1 --on; sleep 4
	b=$(dmesg | grep -c "frame timed out")
	echo "cycle $n: frame timeouts after on: $((b - a)), dpms $(cat /sys/class/drm/card0-DSI-1/dpms)"
done
rm -f /var/tmp/blank.png; $U gdbus call --session --dest org.gnome.Shell.Screenshot --object-path /org/gnome/Shell/Screenshot --method org.gnome.Shell.Screenshot.Screenshot false false /var/tmp/blank.png >/dev/null 2>&1; ls /var/tmp/blank.png 2>/dev/null'
