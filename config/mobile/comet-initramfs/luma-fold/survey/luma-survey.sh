#!/bin/sh
# Read-mostly hardware survey for the Fold (luma.fold_survey=1). Every line goes
# to the journal prefixed "luma-survey:". Writes only: backlight brightness
# (50% and back) and, with luma.fold_survey_suspend=test|deep, the suspend
# steps at the very end. No partitions, no
# radios, no audio, no force-feedback playback.
L() { echo "luma-survey: $*"; }
kv() { for f in "$@"; do [ -r "$f" ] && L "  $f=$(head -c 300 "$f" 2>/dev/null | tr '\n' ' ')"; done; }
# End of survey: with luma.fold_debug=1 (and not luma.fold_hands=1) reboot right
# away, the same systemctl reboot as hook 92, which luma_diag sends to fastboot.
finish() {
	L "end uptime=$(cut -d' ' -f1 /proc/uptime)"
	journalctl --sync 2>/dev/null; sync
	if grep -q "luma.fold_debug=1" /proc/cmdline && ! grep -q "luma.fold_hands=1" /proc/cmdline; then
		L "rebooting to the bootloader"
		journalctl --sync 2>/dev/null; sync
		systemctl reboot
	fi
	exit 0
}
sleep "${SURVEY_DELAY:-45}"
L "begin uptime=$(cut -d' ' -f1 /proc/uptime) kernel=$(uname -r)"

L "== power_supply"
for p in /sys/class/power_supply/*; do
	[ -e "$p" ] || { L "  none"; break; }
	L " supply ${p##*/} -> $(readlink -f "$p/device" 2>/dev/null)"
	sed "s/^/luma-survey:   /" "$p/uevent" 2>/dev/null
done

L "== usb"
for p in /sys/class/typec/*; do [ -e "$p" ] && kv "$p/data_role" "$p/power_role" "$p/power_operation_mode" "$p/orientation"; done
for p in /sys/class/usb_role/*; do [ -e "$p" ] && kv "$p/role"; done
for p in /sys/class/udc/*; do [ -e "$p" ] && kv "$p/state" "$p/current_speed" "$p/function"; done
for p in /sys/class/usb_power_delivery/* /sys/class/typec/*/port*; do [ -e "$p" ] && L "  $p"; done
L "  configfs: $(ls /sys/kernel/config/usb_gadget 2>&1 | tr '\n' ' ')"
L "  usb devices: $(ls /sys/bus/usb/devices 2>/dev/null | tr '\n' ' ')"
for p in /sys/class/udc/*; do [ -e "$p" ] && kv "$p/maximum_speed" "$p/current_speed"; done
for g in /sys/kernel/config/usb_gadget/*; do [ -e "$g" ] && kv "$g/max_speed" "$g/UDC"; done
for f in /sys/kernel/debug/usb/11210000.usb/link_state /sys/kernel/debug/usb/11210000.usb/mode; do kv "$f"; done
grep -E "^(GCTL|GSTS|GUSB2PHYCFG\(0\)|GUSB3PIPECTL\(0\)|DCFG|DCTL|DSTS|GDBGLTSSM|GDBGLSP|GDBGLNMCC|GHWPARAMS3) " /sys/kernel/debug/usb/11210000.usb/regdump 2>/dev/null | tr -s " " | sed "s/^/luma-survey:   dwc3 /"
for t in /sys/kernel/debug/usb/tcpm*; do [ -e "$t" ] && tail -n 40 "$t" | sed "s/^/luma-survey:   tcpm /"; done
# One gadget reconnect now that the TCA is settled: if SuperSpeed trains on a
# reconnect, the boot-time link fell back because of when the lanes switched.
u=/sys/class/udc/11210000.usb
if [ -w "$u/soft_connect" ]; then
	L "  reconnect: before $(cat $u/current_speed)"
	echo disconnect > $u/soft_connect; sleep 2
	echo connect > $u/soft_connect; sleep 6
	L "  reconnect: after $(cat $u/current_speed) state=$(cat $u/state)"
	grep -E "^(DSTS|GDBGLTSSM) " /sys/kernel/debug/usb/11210000.usb/regdump 2>/dev/null | tr -s " " | sed "s/^/luma-survey:   dwc3-after /"
fi
dmesg | grep -iE "usbdrd|SS PHY|TCA|dwc3|eusb" | tail -n 15 | sed "s/^/luma-survey:   k /"

L "== backlight"
for b in /sys/class/backlight/*; do
	[ -e "$b" ] || { L "  none"; break; }
	kv "$b/type" "$b/max_brightness" "$b/brightness" "$b/actual_brightness" "$b/bl_power"
	old=$(cat "$b/brightness"); max=$(cat "$b/max_brightness")
	echo $((max / 2)) > "$b/brightness" 2>/dev/null; r=$?; sleep 2
	L "  ${b##*/} set $((max / 2)) rc=$r actual=$(cat "$b/actual_brightness" 2>/dev/null)"
	echo "$old" > "$b/brightness" 2>/dev/null; sleep 1
	L "  ${b##*/} restored $old actual=$(cat "$b/actual_brightness" 2>/dev/null)"
done
L "  leds: $(ls /sys/class/leds 2>/dev/null | tr '\n' ' ')"
for c in /sys/class/drm/card*-*; do [ -e "$c/status" ] && L "  ${c##*/} $(cat "$c/status") dpms=$(cat "$c/dpms" 2>/dev/null)"; done

L "== iio"
for d in /sys/bus/iio/devices/iio:device*; do
	[ -e "$d" ] || { L "  none"; break; }
	L " ${d##*/} name=$(cat "$d/name" 2>/dev/null)"
	for a in "$d"/in_*_raw "$d"/in_*_input "$d"/in_*_scale; do [ -r "$a" ] && L "   ${a##*/}=$(timeout 3 cat "$a" 2>&1)"; done
done

L " labels: $(for d in /sys/bus/iio/devices/iio:device*; do printf '%s=%s; ' "${d##*/}" "$(cat "$d/label" 2>/dev/null)"; done)"
L " hinge: $(grep -i 'hall' /sys/kernel/debug/gpio 2>/dev/null | tr -s ' ' | head -n 1)"
for i in 1 2 3; do
	for d in /sys/bus/iio/devices/iio:device*; do
		[ "$(cat "$d/name" 2>/dev/null)" = usf_accel ] || continue
		L "  accel $(cat "$d/label") x=$(cat "$d/in_accel_x_raw" 2>&1) y=$(cat "$d/in_accel_y_raw" 2>&1) z=$(cat "$d/in_accel_z_raw" 2>&1) scale=$(cat "$d/in_accel_scale")"
	done
	sleep 1
done
L " udev: $(for d in /sys/bus/iio/devices/iio:device*; do printf '%s[%s %s] ' "${d##*/}" "$(udevadm info -q property -p "$d" 2>/dev/null | sed -n 's/^IIO_SENSOR_PROXY_TYPE=//p')" "$(udevadm info -q property -p "$d" 2>/dev/null | sed -n 's/^ACCEL_LOCATION=//p')"; done)"
L " sensor-proxy: $(gdbus call --system --dest net.hadess.SensorProxy --object-path /net/hadess/SensorProxy --method org.freedesktop.DBus.Properties.GetAll net.hadess.SensorProxy 2>&1 | tr -d '\n' | head -c 600)"
journalctl -b -u iio-sensor-proxy --no-pager -o cat 2>/dev/null | tail -n 15 | sed "s/^/luma-survey:   proxy: /"

L "== upower"
upower -i /org/freedesktop/UPower/devices/DisplayDevice 2>&1 | grep -E "state|percentage|energy|time to|present|power supply" | sed "s/^/luma-survey:   display /"
upower -e 2>&1 | sed "s/^/luma-survey:   dev /"

L "== input"
awk '/^N: Name/{n=$0} /^H: Handlers/{h=$0} /^B: EV=/{e=$0} /^B: FF=/{f=$0} /^B: SW=/{s=$0} /^$/{print "luma-survey:  " n " | " h " | " e (f?" | "f:"") (s?" | "s:""); n=h=e=f=s=""}' /proc/bus/input/devices
for e in /sys/class/input/event*; do
	ff=$(cat "$e/device/capabilities/ff" 2>/dev/null)
	[ -n "$ff" ] && [ "$ff" != 0 ] && L "  force-feedback device ${e##*/} ($(cat "$e/device/name")) ff=$ff (not played)"
done

L "== thermal"
for z in /sys/class/thermal/thermal_zone*; do
	[ -e "$z" ] || { L "  none"; break; }
	L "  ${z##*/} type=$(cat "$z/type") temp=$(cat "$z/temp" 2>&1) policy=$(cat "$z/policy" 2>/dev/null) mode=$(cat "$z/mode" 2>/dev/null) trips=$(cat "$z"/trip_point_*_temp 2>/dev/null | tr '\n' ',')"
done
for c in /sys/class/thermal/cooling_device*; do [ -e "$c" ] && L "  ${c##*/} type=$(cat "$c/type") cur=$(cat "$c/cur_state") max=$(cat "$c/max_state")"; done
for p in /sys/devices/system/cpu/cpufreq/policy*; do
	[ -e "$p" ] && L "  ${p##*/} cpus=$(cat "$p/related_cpus") cur=$(cat "$p/scaling_cur_freq") max=$(cat "$p/scaling_max_freq")/$(cat "$p/cpuinfo_max_freq") gov=$(cat "$p/scaling_governor")"
done

L "== rtc"
for r in /sys/class/rtc/rtc*; do [ -e "$r" ] && kv "$r/name" "$r/date" "$r/time" "$r/since_epoch" "$r/hctosys" "$r/wakealarm" "$r/device/power/wakeup"; done
[ -r /proc/driver/rtc ] && sed "s/^/luma-survey:   /" /proc/driver/rtc
L "  system date $(date -u +%FT%TZ)"

L "== gnss / fingerprint / i2c / spi"
L "  gnss class: $(ls /sys/class/gnss 2>&1 | tr '\n' ' ') dev: $(ls /dev | grep -iE 'gnss|gps' | tr '\n' ' ')"
L "  fp nodes: $(ls /dev | grep -iE 'fpc|goodix|fp|tee' | tr '\n' ' ')"
for d in /sys/bus/i2c/devices/*-*; do [ -e "$d/name" ] && L "  i2c ${d##*/} $(cat "$d/name") drv=$(basename "$(readlink "$d/driver" 2>/dev/null)" 2>/dev/null)"; done
for d in /sys/bus/spi/devices/*; do [ -e "$d" ] && L "  spi ${d##*/} $(cat "$d/modalias" 2>/dev/null) drv=$(basename "$(readlink "$d/driver" 2>/dev/null)" 2>/dev/null)"; done
L "  deferred: $(cat /sys/kernel/debug/devices_deferred 2>/dev/null | tr '\n' ';')"

L "== kernel log (basics)"
dmesg | grep -iE 'max777|max1720|tcpc|tcpm|typec|dwc3|udc|rtc|s2mpg|thermal|tmu|usf|cs40|haptic|gnss|kepler|backlight|power_supply|charger|fuel|battery|hall|gpio-keys|PM: ' | tail -n 120 | sed "s/^/luma-survey: k /"

L "== suspend"
# luma.fold_survey_suspend=test: pm_test devices, platform, processors, core
# (each returns before the firmware call), then one s2idle. =devtest: only
# pm_test devices (prepare + suspend callbacks, nothing else). =deep: one
# suspend-to-RAM (luma.fold_survey_nowifi=1 unbinds Wi-Fi first). Anything
# else: no suspend. Each step syncs the journal first and is logged to kmsg,
# which the RAM console keeps (no_console_suspend).
mode=$(sed -n 's/.*luma\.fold_survey_suspend=\([a-z]*\).*/\1/p' /proc/cmdline)
case "$mode" in
test|devtest|deep) ;;
*)	L "suspend-skipped (luma.fold_survey_suspend=$mode)"
	finish ;;
esac
kv /sys/power/state /sys/power/mem_sleep /sys/power/pm_test /sys/power/wakeup_count
L "  wakeup-capable: $(for w in /sys/class/wakeup/*; do [ -e "$w/name" ] && printf '%s ' "$(cat "$w/name")"; done)"
# Read-only state the firmware sleep depends on, just before the first step.
D=/sys/kernel/debug
L "== pre-suspend state"
L " power domains:"
sed "s/^/luma-survey:   pd /" $D/pm_genpd/pm_genpd_summary 2>/dev/null | head -n 160
L " regulators:"
sed "s/^/luma-survey:   reg /" $D/regulator/regulator_summary 2>/dev/null | head -n 200
L " pcie:"
for c in /sys/devices/platform/soc@0/*.pcie; do
	[ -e "$c" ] && L "  ${c##*/} runtime=$(cat "$c/power/runtime_status" 2>/dev/null) driver=$(basename "$(readlink "$c/driver" 2>/dev/null)" 2>/dev/null)"
done
for d in /sys/bus/pci/devices/*; do
	[ -e "$d" ] && L "  ${d##*/} class=$(cat "$d/class") id=$(cat "$d/vendor"):$(cat "$d/device") speed=$(cat "$d/current_link_speed" 2>/dev/null) width=$(cat "$d/current_link_width" 2>/dev/null) state=$(cat "$d/power_state" 2>/dev/null) driver=$(basename "$(readlink "$d/driver" 2>/dev/null)" 2>/dev/null)"
done
L " modem power:"
for c in /sys/bus/platform/devices/cp-power /sys/bus/platform/devices/*cp*; do
	[ -e "$c" ] && L "  ${c##*/} driver=$(basename "$(readlink "$c/driver" 2>/dev/null)" 2>/dev/null) runtime=$(cat "$c/power/runtime_status" 2>/dev/null)"
done
dmesg | grep -iE "cp-power|s5910|exynos-cp|12100000.pcie" | tail -n 20 | sed "s/^/luma-survey:   k /"
L " clocks on (big blocks):"
awk 'NR>2 && $2>0' $D/clk/clk_summary 2>/dev/null | grep -iE "tpu|aur|g3d|gpu|isp|csis|dpu|dpp|mfc|g2d|gdc|hsi|pcie|ufs|usb|bw|tnr|mcsc|yuvp|rgbp|gse|eh|misc|top" | awk '{print $1, "en="$2, "prep="$3, "rate="$4}' | head -n 120 | sed "s/^/luma-survey:   clk /"
echo 1 > /sys/power/pm_print_times 2>/dev/null
echo 1 > /sys/power/pm_debug_messages 2>/dev/null

stats() { kv /sys/power/suspend_stats/success /sys/power/suspend_stats/fail /sys/power/suspend_stats/last_failed_dev /sys/power/suspend_stats/last_failed_errno /sys/power/suspend_stats/last_failed_step /sys/power/pm_wakeup_irq; }
# step <label> <rtcwake mode> <seconds>
step() {
	before=$(date +%s)
	L "step-start $1 date=$before uptime=$(cut -d' ' -f1 /proc/uptime)"
	echo "luma-survey: step-start $1" > /dev/kmsg
	journalctl --sync 2>/dev/null; sync
	out=$(rtcwake -m "$2" -s "$3" 2>&1); rc=$?
	after=$(date +%s)
	echo "luma-survey: step-end $1 rc=$rc" > /dev/kmsg
	L "step-end $1 rc=$rc slept=$((after - before))s uptime=$(cut -d' ' -f1 /proc/uptime) rtcwake: $(echo "$out" | tr '\n' ' ')"
	echo 0 > /sys/class/rtc/rtc0/wakealarm 2>/dev/null
	stats
	for w in /sys/class/wakeup/*; do
		[ -e "$w/name" ] && [ "$(cat "$w/wakeup_count" 2>/dev/null)" != 0 ] && L "  woke ${w##*/} $(cat "$w/name") wakeup_count=$(cat "$w/wakeup_count")"
	done
	journalctl --sync 2>/dev/null; sync
	return $rc
}

if [ "$mode" = test ] || [ "$mode" = devtest ]; then
	levels="devices platform processors core"
	[ "$mode" = devtest ] && levels=devices
	if [ -w /sys/power/pm_test ]; then
		for lvl in $levels; do
			echo $lvl > /sys/power/pm_test
			step "pm_test-$lvl" mem 15 || { L "pm_test $lvl failed, stopping"; break; }
		done
		echo none > /sys/power/pm_test
	else
		L "no /sys/power/pm_test (PM_DEBUG off)"
	fi
	[ "$mode" = test ] && step s2idle freeze 20
else
	if grep -q "luma.fold_survey_nowifi=1" /proc/cmdline; then
		for d in /sys/bus/pci/drivers/brcmfmac/0000:*; do
			[ -e "$d" ] && echo "${d##*/}" > /sys/bus/pci/drivers/brcmfmac/unbind && L "  unbound Wi-Fi ${d##*/}"
		done
	fi
	L "  aoc: $(lsmod 2>/dev/null | grep -cE '^aoc ') module(s)"
	step deep mem 20
fi
dmesg | grep -iE 'PM: |suspend|resume|wakeup|Freezing|non-boot|psci|luma-survey: step' | tail -n 120 | sed "s/^/luma-survey: k /"
finish
