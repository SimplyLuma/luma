#!/bin/sh
# Fold bring-up: map each touchscreen to its panel (libinput WL_OUTPUT, which
# phoc uses for touch-to-output mapping): Synaptics = outer DSI-1,
# Goodix = inner DSI-2. wlroots rotates touch with the inner panel transform
# (270) while the Goodix already reports in the displayed orientation, so
# rotate it back in libinput (phoc only). LUMA_FOLD_HINGE/LUMA_FOLD_PANEL name the hinge
# and panels for the packaged luma-fold-gate.
[ -d /sysroot/etc/udev ] || exit 0
mkdir -p /sysroot/etc/udev/rules.d
cat > /sysroot/etc/udev/rules.d/90-luma-fold-touch.rules <<"RULES"
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="gpio-keys", ENV{ID_INPUT_SWITCH}=="1", ENV{LUMA_FOLD_HINGE}="1"
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="Synaptics TouchComm Touchscreen", ENV{WL_OUTPUT}="DSI-1", ENV{LUMA_FOLD_PANEL}="outer"
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="Goodix Berlin Capacitive TouchScreen", ENV{WL_OUTPUT}="DSI-2", ENV{LUMA_FOLD_PANEL}="inner", ENV{LIBINPUT_CALIBRATION_MATRIX}="0 -1 1 1 0 0"
RULES
# Mutter shows the inner panel upright (no 270 transform), so the Goodix needs
# no rotation there.
if [ -e /sysroot/usr/lib/systemd/system/gdm.service ]; then
	sed -i 's/, ENV{LIBINPUT_CALIBRATION_MATRIX}="0 -1 1 1 0 0"//' /sysroot/etc/udev/rules.d/90-luma-fold-touch.rules
fi
