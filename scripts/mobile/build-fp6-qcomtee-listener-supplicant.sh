#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output_dir=${1:?usage: build-fp6-qcomtee-listener-supplicant.sh OUTPUT_DIR}
qsee_commit=36e06680cf7f690fccbdcd07abc2a64c4bb061d8
mink_commit=2cee1fdb5b6b89354a8117ac4c8303aaeb8bea4d
qsee_url=https://github.com/wrobelda/qsee-supplicant.git
mink_url=https://github.com/qualcomm/minkipc.git
rpmb_patch=$repo_root/patches/qsee-supplicant/0002-add-ufs-rpmb-listener.patch
gpfs_overlay_patch=$repo_root/patches/qsee-supplicant/0003-preserve-gpfs-tail-on-offset-zero-write.patch
gpfs_overlay_test_patch=$repo_root/patches/qsee-supplicant/0004-test-gpfs-header-overlay.patch
transport=$repo_root/src/fp6-fingerprint-backend/fp6_qcomtee_listener_transport.c
listener_abi=$repo_root/src/fp6-fingerprint-qcomtee/luma-fp6-listener-abi.h

[[ ! -e $output_dir ]] || { echo "refuse to overwrite $output_dir" >&2; exit 1; }
work_dir=$(mktemp -d /tmp/luma-qcomtee-listener.XXXXXX)
cleanup() { find "$work_dir" -depth -delete 2>/dev/null || true; }
trap cleanup EXIT INT TERM

git clone --quiet "$qsee_url" "$work_dir/qsee-supplicant"
git -C "$work_dir/qsee-supplicant" checkout --quiet "$qsee_commit"
git clone --quiet "$mink_url" "$work_dir/minkipc"
git -C "$work_dir/minkipc" checkout --quiet "$mink_commit"
source_dir=$work_dir/minkipc/listeners/librpmbservice
source_date_epoch=$(git -C "$work_dir/qsee-supplicant" show -s --format=%ct "$qsee_commit")
export SOURCE_DATE_EPOCH=$source_date_epoch
repro_flags="-O2 -g -ffile-prefix-map=$work_dir=/usr/src/luma-qcomtee-listener -fdebug-prefix-map=$work_dir=/usr/src/luma-qcomtee-listener"
cp "$source_dir"/{rpmb.c,rpmb_emmc.c,rpmb_ufs.c,rpmb_logging.c} \
	"$work_dir/qsee-supplicant/src/"
cp "$source_dir"/{rpmb.h,rpmb_private.h,rpmb_ufs.h,rpmb_logging.h,rpmb_service.h} \
	"$work_dir/qsee-supplicant/include/"
git -C "$work_dir/qsee-supplicant" apply "$rpmb_patch"
git -C "$work_dir/qsee-supplicant" apply "$gpfs_overlay_patch"
git -C "$work_dir/qsee-supplicant" apply "$gpfs_overlay_test_patch"

cc -D_GNU_SOURCE $repro_flags -std=c11 -Wall -Wextra -Werror \
	-I"$work_dir/qsee-supplicant/include" -I"$(dirname "$listener_abi")" \
	-o "$work_dir/luma-qcomtee-listener-supplicant" \
	"$work_dir/qsee-supplicant/src/main.c" "$transport" \
	"$work_dir/qsee-supplicant/src/path.c" \
	"$work_dir/qsee-supplicant/src/services.c" \
	"$work_dir/qsee-supplicant/src/handle_db.c" \
	"$work_dir/qsee-supplicant/src/fs.c" \
	"$work_dir/qsee-supplicant/src/gpfs.c" \
	"$work_dir/qsee-supplicant/src/rpmb_listener.c" \
	"$work_dir/qsee-supplicant/src/rpmb.c" \
	"$work_dir/qsee-supplicant/src/rpmb_emmc.c" \
	"$work_dir/qsee-supplicant/src/rpmb_ufs.c" \
	"$work_dir/qsee-supplicant/src/rpmb_logging.c" \
	"$work_dir/qsee-supplicant/src/notify.c" -pthread

install -d -m 0755 "$output_dir"
install -m 0755 "$work_dir/luma-qcomtee-listener-supplicant" "$output_dir/"
sha256sum "$output_dir/luma-qcomtee-listener-supplicant" >"$output_dir/manifest.sha256"
cat "$output_dir/manifest.sha256"
