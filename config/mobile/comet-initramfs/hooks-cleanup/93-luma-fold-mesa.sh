#!/bin/sh
# Fold bring-up: install the private Mesa (Mali-G715 / panthor, see
# pixel9-fold-m0/mesa) to /var/lib/luma-fold/mesa. With luma.fold_gpu=1 put it
# in the systemd user manager's environment, so phosh, portals and D-Bus
# activated apps load it; hook 90 does the same for phoc. GTK 4 is kept on
# its GL renderer: the Vulkan drivers in the image do not know the G715, so
# GTK would fall through to lavapipe (CPU) and never show a frame.
[ -d /sysroot/var ] && [ -f /luma-fold/mesa/BUILD-INFO ] || exit 0
m=/sysroot/var/lib/luma-fold/mesa
if ! cmp -s /luma-fold/mesa/BUILD-INFO $m/BUILD-INFO; then
	rm -rf $m.new; mkdir -p /sysroot/var/lib/luma-fold
	cp -a /luma-fold/mesa $m.new && rm -rf $m && mv $m.new $m
fi
e=/sysroot/etc/environment.d/90-luma-fold-gpu.conf
rm -f /sysroot/etc/systemd/user/mobi.phosh.Shell.service.d/99-luma-fold-gpu.conf
if grep -q "luma.fold_gpu=1" /proc/cmdline; then
	mkdir -p /sysroot/etc/environment.d
	m=/var/lib/luma-fold/mesa
	printf "LD_LIBRARY_PATH=$m/lib64\n__EGL_VENDOR_LIBRARY_FILENAMES=$m/share/glvnd/egl_vendor.d/50_mesa.json\nGBM_BACKENDS_PATH=$m/lib64/gbm\nLIBGL_DRIVERS_PATH=$m/lib64/dri\nGSK_RENDERER=ngl\nGDK_DISABLE=vulkan\n" > $e
else
	rm -f $e
fi
