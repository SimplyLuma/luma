#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
output_dir=${1:-$repo_root/build/mobile/fp6-physical/fp6-qsee-supplicant-rpmb-v1}
qsee_commit=36e06680cf7f690fccbdcd07abc2a64c4bb061d8
mink_commit=2cee1fdb5b6b89354a8117ac4c8303aaeb8bea4d
qsee_url=https://github.com/wrobelda/qsee-supplicant.git
mink_url=https://github.com/qualcomm/minkipc.git
patch_file=$repo_root/patches/qsee-supplicant/0002-add-ufs-rpmb-listener.patch

for command in git make sha256sum; do
	command -v "$command" >/dev/null || {
		echo "missing required command: $command" >&2
		exit 1
	}
done

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT
git clone --quiet "$qsee_url" "$work_dir/qsee-supplicant"
git -C "$work_dir/qsee-supplicant" checkout --quiet "$qsee_commit"
git clone --quiet "$mink_url" "$work_dir/minkipc"
git -C "$work_dir/minkipc" checkout --quiet "$mink_commit"
source_date_epoch=$(git -C "$work_dir/qsee-supplicant" show -s --format=%ct "$qsee_commit")
export SOURCE_DATE_EPOCH=$source_date_epoch
repro_cflags="-O2 -g -ffile-prefix-map=$work_dir=/usr/src/luma-qsee-rpmb -fdebug-prefix-map=$work_dir=/usr/src/luma-qsee-rpmb"

source_dir=$work_dir/minkipc/listeners/librpmbservice
while read -r expected name; do
	actual=$(sha256sum "$source_dir/$name" | cut -d' ' -f1)
	[[ $actual == "$expected" ]] || {
		echo "pinned Qualcomm source hash mismatch: $name" >&2
		exit 1
	}
done <<'EOF'
e3b2f627ea3d308d1c700745aecc22612b401040b834b9b6813c3dbdb8e27b30 rpmb.c
36b91fd6b197de994d4911c69f85bd3682cc4529882d27fd6c2b34f3bdc60f56 rpmb.h
19993be023297daf1b873536632eee9622e4da9f92bb64393930ec7d4c2c6018 rpmb_private.h
753f2a7d30c947d0c12d1d2bf668e7c8ce7204caf8eaaa418884b73579dd80e7 rpmb_ufs.c
97ed857883dfcc1c9d9eb76a6d834f61293dc45cd4dcc107ee8a59cf5fff8adb rpmb_ufs.h
f65ae1cba44c75420ccee4ecc5aeb5406ea1476631b433c31e8e5a0b10bcdffd rpmb_emmc.c
fbafd8dde18b61f52d093ca210c3cba49acf81f40918aad93e1ebf1c2e2d6e96 rpmb_logging.c
903f17d71af5bb63daa802987dd52805db4c668834df93891db2af9ec2b4fd79 rpmb_logging.h
e30779cda67b630c700ec5828218faf96c5fb023c7d9f83254f6c98e14720270 rpmb_service.h
EOF

cp "$source_dir"/{rpmb.c,rpmb_emmc.c,rpmb_ufs.c,rpmb_logging.c} \
	"$work_dir/qsee-supplicant/src/"
cp "$source_dir"/{rpmb.h,rpmb_private.h,rpmb_ufs.h,rpmb_logging.h,rpmb_service.h} \
	"$work_dir/qsee-supplicant/include/"
git -C "$work_dir/qsee-supplicant" apply "$patch_file"

make -C "$work_dir/qsee-supplicant" clean
make -C "$work_dir/qsee-supplicant" CFLAGS="$repro_cflags" check
make -C "$work_dir/qsee-supplicant" CFLAGS="$repro_cflags" qsee-supplicant
cc -D_GNU_SOURCE $repro_cflags -std=c11 -Wall -Wextra -Werror \
	-I"$work_dir/qsee-supplicant/include" \
	-o "$work_dir/fp6-rpmb-preflight" \
	"$repo_root/src/fp6-fingerprint-backend/fp6_rpmb_preflight.c" \
	"$work_dir/qsee-supplicant/src/rpmb.c" \
	"$work_dir/qsee-supplicant/src/rpmb_emmc.c" \
	"$work_dir/qsee-supplicant/src/rpmb_ufs.c" \
	"$work_dir/qsee-supplicant/src/rpmb_logging.c"

install -d -m 0755 "$output_dir"
install -m 0755 "$work_dir/qsee-supplicant/qsee-supplicant" \
	"$output_dir/qsee-supplicant"
install -m 0755 "$work_dir/fp6-rpmb-preflight" \
	"$output_dir/fp6-rpmb-preflight"
{
	echo "QSEE_SUPPLICANT_COMMIT=$qsee_commit"
	echo "MINKIPC_COMMIT=$mink_commit"
	echo "RPMB_LISTENER_ID=8192"
	echo "RPMB_DEVICE=/dev/bsg/0:0:0:49476"
	echo "RPMB_KEY_PROVISIONING=false"
	echo "RPMB_ERASE=false"
	sha256sum "$output_dir/qsee-supplicant"
	sha256sum "$output_dir/fp6-rpmb-preflight"
} >"$output_dir/manifest.env"
cat "$output_dir/manifest.env"
