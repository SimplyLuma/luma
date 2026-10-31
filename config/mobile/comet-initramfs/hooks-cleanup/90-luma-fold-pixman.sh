#!/bin/sh
# Pixel 9 Pro Fold bring-up: the Mali GPU has no driver yet, so start Phoc with
# the wlroots pixman (CPU) renderer. greetd's initial session skips PAM auth, so
# pam_env and systemd.setenv never reach it; set it on the greetd commands.
# wlroots 0.20 ignores libinput WL_OUTPUT, so preload a shim that names each
# touchscreen's panel for phoc (patches/phoc/touch-output-preload.c).
# With luma.fold_debug=1 also enable GLib debug messages (phoc logs via
# systemd-cat -t phoc).
cfg=/sysroot/etc/greetd/config.toml
[ -f "$cfg" ] || exit 0
envs="WLR_RENDERER=pixman"
# luma.fold_gpu=1: GLES2 on panthor with the private Mesa from hook 93
m=/var/lib/luma-fold/mesa
grep -q "luma.fold_gpu=1" /proc/cmdline && envs="WLR_RENDERER=gles2 LD_LIBRARY_PATH=$m/lib64 __EGL_VENDOR_LIBRARY_FILENAMES=$m/share/glvnd/egl_vendor.d/50_mesa.json GBM_BACKENDS_PATH=$m/lib64/gbm LIBGL_DRIVERS_PATH=$m/lib64/dri"
# wlroots with the libinput output-name patch maps touch itself
grep -q libinput_device_get_output_name /sysroot/usr/lib64/libwlroots-0.20.so 2>/dev/null ||
[ ! -f /luma-fold/luma-fold-phoc-touch.so ] ||
	envs="$envs LD_PRELOAD=/var/lib/luma-fold/luma-fold-phoc-touch.so LUMA_TOUCH_OUTPUTS=Goodix:DSI-2,Synaptics:DSI-1"
grep -q "luma.fold_debug=1" /proc/cmdline && envs="$envs G_MESSAGES_DEBUG=all"
# debug: the compositor's stderr is otherwise lost; capture the session
pre="env"
grep -q "luma.fold_debug=1" /proc/cmdline && pre="/usr/bin/systemd-cat -t phoc-session env"
sed -i "s|^command = \".* /usr/libexec/|command = \"/usr/libexec/|; s|^command = \"/usr/libexec/|command = \"$pre $envs /usr/libexec/|" "$cfg"
grep "^command" "$cfg" > /dev/kmsg
