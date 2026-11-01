#!/bin/bash
# luma-tokay-v15: v10 kernel (luma/tokay-daily1) + initramfs tokay-luma-initramfs-v2 (hook 89: GDM
# autologin, Mobile presentation) for the shared Luma shell image. Usage: OSTREE_PATH=/ostree/boot.N/luma/<bootcsum>/0 build-tokay-v11.sh
[ -n "${OSTREE_PATH:-}" ] || { echo "OSTREE_PATH required"; exit 1; }
cd /mnt/luma-secondary/luma-build/pixel9-fold
export PROBE_W=/dev/shm/pixel9-tokay-v15 PROBE_BRANCH=luma/tokay-daily1 PROBE_DTB=zumapro-tokay
export PROBE_VERSION=luma-tokay-v15 PROBE_INITRAMFS_DIR=/mnt/luma-secondary/luma-build/pixel9-fold/inputs/tokay-luma-initramfs-v2
export PROBE_CMDLINE="$(sed -n "s/^CMDLINE=//p" candidates/luma-v60/manifest.env | sed -e "s/ luma.fold_debug=1//" -e "s|ostree=[^ ]*|ostree=$OSTREE_PATH|") luma.diag_restart=0 exynosdrm.pkt_go=1"
export PROBE_MODULES="$(cat probe/luma-boot.modules) aoc-channel aoc-usf aoc-usf-iio s5xxx_modem gnss-kepler"
export PROBE_EXTRA_CONFIG="$(cat probe/luma-boot.config) --enable F2FS_FS --enable F2FS_FS_SECURITY --enable DYNAMIC_DEBUG" JOBS=16
exec luma-heavy bash probe/build-tokay.sh probe candidates/luma-tokay-v15
