#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# postmarketOS-initramfs hook for the Fairphone 6 boot image (/hooks-cleanup:
# runs after ostree-prepare-root, with the deployment at /sysroot and the
# shared stateroot /var mounted at /sysroot/var, just before switch_root).
#
# Seeds the modem's private rmtfs store, /var/lib/luma/rmtfs, from modemst1,
# modemst2, fsc, fsg and study, in the layout luma-fp6-modem-store writes
# (luma-fp6-carrier-adapter) and luma-fp6-rmtfs serves.
#
# Why here: after a Fairphone factory flash the bootloader erases modemst1/2
# on the first normal boot (`fastboot boot` skips that). The kit's first start
# is `fastboot boot`, but luma-device-layer restarts the phone normally once
# the device kit is layered, and luma-fp6-modem-store.service only exists in
# the layered deployment. Seeding in this initramfs on that first start copies
# the modem settings, IMEI included, before any normal boot can erase them,
# whatever ends the first boot.
#
# The partitions are only read. Nothing here writes a modem partition, logs
# an identifier or copies from erased (all-zero) modemst. An existing store is
# left to luma-fp6-modem-store.service. Files made before the SELinux policy
# loads have no label, so this also adds a runtime unit that labels the store
# early on the next start of systemd (the marker rmtfs.relabel keeps asking
# until that has run). A failure here never stops the boot.
#
# LUMA_FP6_TEST_ROOT (tests only) prefixes /sys, /dev, /sysroot and /run and
# accepts regular files in place of block devices.

T=${LUMA_FP6_TEST_ROOT:-}
sysfs=$T/sys
devdir=$T/dev
var=$T/sysroot/var
run=$T/run
log=${LUMA_FP6_MODEM_STORE_LOG:-/dev/kmsg}
model='The Fairphone (Gen. 6)'
max_bytes=$((64 * 1024 * 1024))
modemst_bytes=$((10 * 1024 * 1024))
zero_10mib=e5b844cc57f57094ea4585e235f36c78c1cd222262bb89d53c94dcb4d6b3e55d
store=$var/lib/luma/rmtfs
stage=$var/lib/luma/rmtfs.new
marker=$var/lib/luma/rmtfs.relabel
manifest=luma-fp6-modem-store.json
unit=luma-fp6-modem-store-label.service

event() {
	echo "luma-fp6-modem-store: event=luma_fp6_modem_store source=initramfs $*" >>"$log" 2>/dev/null || :
}

# The one partition named $1: its /dev path, from sysfs. Fails when there is
# none or more than one.
partition() {
	local found uevent name
	found=
	for uevent in "$sysfs"/class/block/*/uevent; do
		[ -f "$uevent" ] || continue
		grep -qx "PARTNAME=$1" "$uevent" || continue
		[ -z "$found" ] || return 1
		name=$(sed -n 's/^DEVNAME=//p' "$uevent")
		[ -n "$name" ] || return 1
		found=$devdir/$name
	done
	[ -n "$found" ] || return 1
	if [ -n "$T" ]; then [ -f "$found" ] || return 1; else [ -b "$found" ] || return 1; fi
	echo "$found"
}

bytes_of() {
	local sectors
	sectors=$(cat "$sysfs/class/block/${1##*/}/size" 2>/dev/null) || return 1
	case "$sectors" in '' | *[!0-9]*) return 1 ;; esac
	echo $((sectors * 512))
}

digest() {
	local out
	out=$(sha256sum <"$1") || return 1
	echo "${out%% *}"
}

# Label the store early on the next systemd start, before rmtfs, the store
# service and luma-device-layer (whose restart follows the first start).
add_label_unit() {
	mkdir -p "$run/systemd/system/sysinit.target.wants" || return 1
	cat >"$run/systemd/system/$unit" <<'UNIT' || return 1
[Unit]
Description=Label the Fairphone 6 modem store seeded in the initramfs
DefaultDependencies=no
RequiresMountsFor=/var/lib/luma
Conflicts=shutdown.target
After=local-fs.target
Before=sysinit.target shutdown.target rmtfs.service luma-fp6-modem-store.service luma-device-layer.service
ConditionPathExists=/var/lib/luma/rmtfs.relabel
ConditionSecurity=selinux

[Service]
Type=oneshot
ExecStart=/usr/sbin/restorecon -F /var/lib/luma
ExecStart=/usr/sbin/restorecon -RF /var/lib/luma/rmtfs
ExecStart=/usr/bin/rm -f /var/lib/luma/rmtfs.relabel
UNIT
	ln -sf "../$unit" "$run/systemd/system/sysinit.target.wants/$unit"
}

fail() {
	event "state=failed reason=$1 partitions_written=0 identifiers_logged=0"
	rm -rf "$stage"
	exit 0
}

[ "$(tr -d '\0' <"$sysfs/firmware/devicetree/base/model" 2>/dev/null)" = "$model" ] || exit 0
[ -d "$var/lib" ] || exit 0
# The stateroot /var, as ostree-prepare-root mounted it (a bind mount from the
# same filesystem, which busybox mountpoint cannot see).
[ -n "$T" ] || awk -v m="$var" '$5 == m { f = 1 } END { exit !f }' /proc/self/mountinfo || exit 0

if [ -e "$store" ] || [ -L "$store" ]; then
	if [ -f "$marker" ]; then
		add_label_unit || event "state=failed reason=label_unit identifiers_logged=0"
	fi
	exit 0
fi

labels="modemst1 modemst2 fsc fsg study"
for label in $labels; do
	partition "$label" >/dev/null || fail "partition_missing_$label"
done
# Both modemst all-zero: erased and not rebuilt by Android. Never seed from
# that; luma-fp6-modem-store.service reports efs-missing.
blank=0
for label in modemst1 modemst2; do
	device=$(partition "$label")
	[ "$(bytes_of "$device")" = "$modemst_bytes" ] || fail "partition_size_$label"
	[ "$(digest "$device")" = "$zero_10mib" ] && blank=$((blank + 1))
done
if [ "$blank" = 2 ]; then
	event "state=deferred reason=modemst_blank partitions_written=0 identifiers_logged=0"
	exit 0
fi

umask 077
rm -rf "$stage"
[ -d "$var/lib/luma" ] || mkdir -m 0755 "$var/lib/luma" || fail stage
mkdir -m 0700 "$stage" || fail stage
rows=
for label in $labels; do
	case $label in
		modemst1) file=modem_fs1 ;;
		modemst2) file=modem_fs2 ;;
		fsc) file=modem_fsc ;;
		fsg) file=modem_fsg ;;
		study) file=modem_study ;;
	esac
	device=$(partition "$label") || fail "partition_missing_$label"
	bytes=$(bytes_of "$device") || fail "partition_size_$label"
	[ "$bytes" -gt 0 ] && [ "$bytes" -le "$max_bytes" ] || fail "partition_size_$label"
	source_sha=$(digest "$device") || fail "read_$label"
	dd if="$device" of="$stage/$file" bs=1048576 2>/dev/null || fail "copy_$label"
	copied=$(wc -c <"$stage/$file") || fail "copy_size_$label"
	[ $((copied)) -eq "$bytes" ] || fail "copy_size_$label"
	[ "$(digest "$stage/$file")" = "$source_sha" ] || fail "copy_digest_$label"
	# The partition must not have changed while it was copied.
	[ "$(digest "$device")" = "$source_sha" ] || fail "partition_changed_$label"
	if [ "$label" = fsg ] && [ "$(head -c "$bytes" /dev/zero | sha256sum | cut -d' ' -f1)" = "$source_sha" ]; then
		# No factory golden copy: this phone needs Fairphone's own recovery.
		fail fsg_blank
	fi
	rows="${rows:+$rows,}\"$label\":{\"file\":\"$file\",\"bytes\":$bytes,\"sha256\":\"$source_sha\"}"
done
: >"$stage/modem_tunning" || fail stage
printf '{"schema":"org.projectluma.fp6-modem-store/v1","seeded_utc":"%s","partitions_written":false,"sources":{%s}}\n' \
	"$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$rows" >"$stage/$manifest" || fail stage
chmod 0600 "$stage"/* || fail stage
sync
# The marker first: the store is never left unlabelled without it.
: >"$marker" || fail stage
mv "$stage" "$store" || { rm -f "$marker"; fail promote; }
sync
add_label_unit || event "state=failed reason=label_unit identifiers_logged=0"
event "state=seeded partitions_written=0 identifiers_logged=0"
exit 0
