#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# postmarketOS-initramfs hook (/hooks-extra, run before the root is mounted):
# point /ostree/root.<slot> at ostree's default deployment, so the boot image
# no longer names a deployment and OTA updates, `rpm-ostree rollback` and
# greenboot's fallback take effect. luma_prepare_ostree_root in
# init_functions.sh then hands that link to ostree-prepare-root unchanged.
#
# The root is mounted read-write only for this and unmounted again. When it
# cannot be found or mounted here (encrypted root, unknown filesystem), the
# existing link boots, as before. See /usr/lib/luma/ostree-boot.sh.
#
# The boot image's command line keeps androidboot.slot_suffix and an ostree=
# argument that names the slot link (ostree=/ostree/root.a): libostree follows
# the link when the slot suffix is set, and ostree-remount, ostree-boot-complete
# and rpm-ostree-fix-shadow-mode only run with an ostree= argument present.
[ -e /etc/luma-fp6-ostree ] || exit 0
[ -r /usr/lib/luma/ostree-boot.sh ] || exit 0
. /usr/lib/luma/ostree-boot.sh
. /init_functions.sh

part=
i=0
while :; do
	PMOS_ROOT=
	find_root_partition part
	[ -z "$part" ] || [ ! -b "$part" ] || break
	[ "$i" -lt 20 ] || { luma_ostree_log "root partition not found; booting the existing slot link"; exit 0; }
	sleep 1
	i=$((i + 1))
done
type="$(get_partition_type "$part")"
case "$type" in
	ext4|f2fs|btrfs|xfs) ;;
	*) luma_ostree_log "root is $type; booting the existing slot link"; exit 0 ;;
esac
modprobe "$type" 2>/dev/null

M=/run/luma-ostree-root
mkdir -p "$M"
mount -t "$type" -o "rw${rootfsopts:-}" "$part" "$M" ||
	{ luma_ostree_log "cannot mount $part; booting the existing slot link"; exit 0; }
if luma_ostree_resolve "$M"; then
	luma_ostree_apply "$M" "$(cat /proc/cmdline)" || luma_ostree_log "cannot update the slot link"
	# greenboot keeps its boot counter in /boot/grub2/grubenv
	[ -d "$M/boot/grub2" ] || mkdir -p "$M/boot/grub2"
fi
sync
umount "$M"
exit 0
