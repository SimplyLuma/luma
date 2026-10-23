#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the Titan 2 Elite Wi-Fi/Bluetooth kernel modules (MediaTek CONNSYS:
# conninfra, gen4m Wi-Fi, BT) out of tree against the Titan kernel.
#   build-titan2e-connsys-modules.sh [kernel [output dir]]
# kernel is the build-titan2e-kernel.sh output (default) or a built kernel tree.
# Sources: packaging/titan2e-connsys/{vendor_mediatek_kernel_modules,
# kernel_device_modules} (MediaTek's MT6878 GPL releases, the files this build
# uses, vendored unmodified) plus packaging/titan2e-connsys/titan2e-connsys.patch.
set -euo pipefail
repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
kdir=$(realpath "${1:-$repo_root/build/packages/titan2e-kernel}")
[ ! -d "$kdir/kbuild/obj" ] || kdir=$kdir/kbuild/obj
out=${2:-$repo_root/build/titan2e-connsys}
[ -e "$kdir/Module.symvers" ] || { echo "$kdir: not a built kernel tree" >&2; exit 1; }
[ ! -e "$out" ] || { echo "Existing output must be retained: $out" >&2; exit 1; }

# Vendored unmodified, checked against their SHA-256:
#   vendor_mediatek_kernel_modules  github.com/motorola-mt6878/vendor_mediatek_kernel_modules
#                                   1c51f57c78be8acb3c51bc1911fffcd7b5c90184
#   kernel_device_modules           github.com/motorola-mt6878/kernel_device_modules
#                                   6586afaa0622648a6e740fb4a83f4b8057346bc2
pkg=$repo_root/packaging/titan2e-connsys
for tree in vendor_mediatek_kernel_modules kernel_device_modules; do
	(cd "$pkg/$tree" && sha256sum --quiet -c "$pkg/$tree.sha256" &&
		find . -type l | LC_ALL=C sort | sed 's|^\./||' | while read -r l; do printf '%s -> %s\n' "$l" "$(readlink "$l")"; done |
		cmp -s - "$pkg/$tree.links") ||
		{ echo "$pkg/$tree differs from $tree.sha256 or $tree.links" >&2; exit 1; }
done

mkdir -p "$(dirname "$out")"
work=$(mktemp -d "${out}.work.XXXXXX")
cp -R "$pkg/kernel_device_modules" "$work/kdm"
src=$work/src
mkdir -p "$src" "$work/top/vendor/mediatek/kernel_modules"
cp -R "$pkg/vendor_mediatek_kernel_modules/connectivity/"{bt,common,conninfra,wlan} "$src/"
cp -R "$work/kdm/drivers/misc/mediatek/connectivity" "$src/connadp"
ln -s "$src" "$work/top/vendor/mediatek/kernel_modules/connectivity"
ln -s "$work/kdm" "$work/top/kdm"
patch -d "$src" -p1 -s <"$repo_root/packaging/titan2e-connsys/titan2e-connsys.patch"

mk() { # mk <dir> [make vars...]
	local d=$1; shift
	make -C "$kdir" M="$src/$d" ARCH=arm64 LLVM=1 -j"${JOBS:-8}" \
		CONFIG_DEBUG_INFO_BTF_MODULES= TOP="$work/top" DEVICE_MODULES_PATH="$work/top/kdm" \
		TARGET_BUILD_VARIANT=userdebug \
		KCFLAGS="-I$src/compat-inc -include $src/compat.h -Wno-error -Wno-missing-prototypes -Wno-missing-variable-declarations -Wno-unused-but-set-variable -Wno-unused-variable -Wno-unused-function" \
		"$@" modules
}
sym="$src/connadp/Module.symvers"
mk connadp CONFIG_MTK_COMBO=m
mk conninfra CONFIG_MTK_COMBO_CHIP_CONSYS_6878=y KBUILD_EXTRA_SYMBOLS="$sym"
sym="$sym $src/conninfra/Module.symvers"
mk wlan/adaptor/build/connac2x KBUILD_EXTRA_SYMBOLS="$sym"
mk bt/mt66xx/6878 KBUILD_EXTRA_SYMBOLS="$sym"
mk wlan/core/gen4m MODULE_NAME=wlan_drv_gen4m_6878 SEGMENT=SP CONFIG_MTK_COMBO_WIFI_HIF=axi \
	CONFIG_RX_NAPI_THREADED=n CONFIG_RX_NAPI_WORK_SUPPORT=n CONFIG_MTK_WIFI_CONNFEM_SUPPORT=n \
	CONFIG_MTK_CONNSYS_DEDICATED_LOG_PATH=y \
	KBUILD_EXTRA_SYMBOLS="$sym $src/wlan/adaptor/build/connac2x/Module.symvers"

krel=$(cat "$kdir/include/config/kernel.release")
dest=$work/output/usr/lib/modules/$krel/extra/titan2e-connsys
mkdir -p "$dest"
for k in connadp/connadp.ko conninfra/conninfra.ko wlan/adaptor/build/connac2x/wmt_chrdev_wifi_connac2.ko \
	wlan/core/gen4m/wlan_drv_gen4m_6878.ko bt/mt66xx/6878/bt_drv_6878.ko; do
	llvm-strip --strip-debug -o "$dest/$(basename "$k")" "$src/$k"
done
mv "$work/output" "$out"
rm -rf "$work"
printf 'built %s for %s\n' "$out" "$krel"
