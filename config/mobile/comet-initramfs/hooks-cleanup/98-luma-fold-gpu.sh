#!/bin/sh
# Fold bring-up: with luma.fold_debug=1, log GPU, Mesa and DVFS state to the
# journal at 40 s and 150 s; otherwise remove the service.
s=/sysroot/etc/systemd/system
[ -d $s ] || exit 0
if grep -q "luma.fold_debug=1" /proc/cmdline; then
	cat > $s/luma-fold-gpu-diag.sh <<"SH"
#!/bin/sh
for t in 40 110; do
	sleep $t
	echo "== gpu diag"
	rpm -q mesa-dri-drivers mesa-libEGL mesa-libgbm mesa-vulkan-drivers linux-firmware
	ls -l /dev/dri /usr/lib64/dri 2>&1 | head -40
	ls -lR /usr/lib/firmware/arm/mali 2>&1
	cat /sys/kernel/debug/dri/*/name /sys/class/drm/renderD*/device/uevent 2>&1
	for p in /sys/devices/system/cpu/cpufreq/policy*; do
		echo "$p $(cat $p/related_cpus) gov=$(cat $p/scaling_governor) cur=$(cat $p/scaling_cur_freq) hw=$(cat $p/cpuinfo_cur_freq 2>&1) min=$(cat $p/scaling_min_freq) max=$(cat $p/scaling_max_freq)"
		tr "\n" " " < $p/stats/time_in_state; echo
	done
	for d in /sys/class/devfreq/*; do
		echo "$d $(cat $d/name) gov=$(cat $d/governor) cur=$(cat $d/cur_freq)"
		cat $d/trans_stat 2>/dev/null | tail -2
	done
	for p in $(pgrep -x "phoc|phosh"); do
		echo "$(cat /proc/$p/comm) $p maps:"
		grep -o -E "/[^ ]*(dri|gallium|libEGL|libGLES|pixman)[^ ]*" /proc/$p/maps | sort -u
		ls -l /proc/$p/fd 2>/dev/null | grep dri
	done
	command -v eglinfo >/dev/null && EGL_LOG_LEVEL=debug eglinfo -B -p gbm 2>&1 | head -40
	dmesg | grep -i -E "panthor|mali|cpufreq|opp|devfreq" | tail -40
done
SH
	chmod 755 $s/luma-fold-gpu-diag.sh
	printf "[Unit]\nDescription=Fold bring-up GPU diagnostics\n[Service]\nExecStart=/bin/sh /etc/systemd/system/luma-fold-gpu-diag.sh\n[Install]\nWantedBy=multi-user.target\n" > $s/luma-fold-gpu-diag.service
	mkdir -p $s/multi-user.target.wants
	ln -sf ../luma-fold-gpu-diag.service $s/multi-user.target.wants/luma-fold-gpu-diag.service
else
	rm -f $s/luma-fold-gpu-diag.service $s/luma-fold-gpu-diag.sh $s/multi-user.target.wants/luma-fold-gpu-diag.service
fi
